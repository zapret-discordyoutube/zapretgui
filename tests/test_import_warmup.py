from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

from main import entry
from main.qtawesome_font_policy import (
    QT_AWESOME_ALLOWED_PREFIXES,
    configure_qtawesome_module,
    select_qtawesome_bundles,
)


class ImportWarmupTests(unittest.TestCase):
    """Тяжёлые импорты греются в фоне, чтобы не рвать кадр посреди работы."""

    def test_actual_qtawesome_policy_renders_every_literal_source_icon(self) -> None:
        import qtawesome
        from PyQt6.QtWidgets import QApplication

        configure_qtawesome_module(qtawesome)
        app = QApplication.instance() or QApplication([])
        source_root = Path(__file__).resolve().parents[1] / "src"
        pattern = re.compile(
            r"['\"]((?:fa5s|fa5b|mdi|ri)\.[A-Za-z0-9_-]+)['\"]",
            flags=re.IGNORECASE,
        )
        icon_names = {
            icon_name
            for path in source_root.rglob("*.py")
            for icon_name in pattern.findall(path.read_text(encoding="utf-8"))
        }

        self.assertTrue(icon_names)
        for icon_name in sorted(icon_names):
            with self.subTest(icon_name=icon_name):
                self.assertFalse(qtawesome.icon(icon_name).isNull())
        self.assertIsNotNone(app)

    def test_asyncio_is_warmed_up_after_window_is_ready(self) -> None:
        # Первое обращение к Telegram Proxy тянет asyncio вместе с
        # asyncio.windows_events — в логе это давало рывок на ~64 мс.
        # Окну он не нужен, поэтому греется уже после готовности интерфейса.
        from main.post_startup_import_warmup import AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES

        self.assertIn("asyncio", AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES)
        self.assertNotIn("asyncio", entry.IMPORT_WARMUP_MODULES)

    def test_after_interactive_warmup_starts_only_when_window_is_ready(self) -> None:
        from main import post_startup_import_warmup as warmup

        class _Signal:
            def __init__(self) -> None:
                self.callbacks = []

            def connect(self, callback) -> None:
                self.callbacks.append(callback)

        signal = _Signal()
        startup_host = SimpleNamespace(
            startup_interactive_ready=signal,
            startup_state=SimpleNamespace(interactive_logged=False),
            is_alive=lambda: True,
        )
        scheduled: list[tuple[int, object]] = []
        with (
            patch.object(warmup, "start_daemon_thread") as start_thread,
            patch.object(
                warmup,
                "schedule_after",
                side_effect=lambda delay_ms, callback: scheduled.append((delay_ms, callback)),
            ),
        ):
            warmup.install_after_interactive_import_warmup(startup_host, log_startup_metric=lambda *_a: None)
            start_thread.assert_not_called()

            signal.callbacks[0]()

            # Первую секунду интерфейс занят собой: фоновый импорт в это время
            # делил бы с GUI-потоком GIL, поэтому сразу не стартует.
            start_thread.assert_not_called()
            self.assertEqual(
                [delay for delay, _callback in scheduled],
                [warmup.AFTER_INTERACTIVE_IMPORT_WARMUP_DELAY_MS],
            )
            self.assertGreaterEqual(warmup.AFTER_INTERACTIVE_IMPORT_WARMUP_DELAY_MS, 1_000)

            scheduled[0][1]()

        start_thread.assert_called_once()
        self.assertEqual(start_thread.call_args.args[0], "import-warmup-after-interactive")

    def test_tray_and_telegram_proxy_modules_are_warmed_in_background(self) -> None:
        # Значок в трее создаётся в GUI-потоке: первый импорт его модулей и
        # пакета Telegram Proxy прямо там задерживал кадр на ~65 мс.
        from main.post_startup_import_warmup import AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES

        for name in ("tray", "telegram_proxy.runtime.commands", "telegram_proxy.manager"):
            with self.subTest(module=name):
                self.assertIn(name, AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES)

    def test_warmed_modules_create_no_qt_objects_at_import(self) -> None:
        # Импорт идёт в фоновом потоке: QObject, созданный там на уровне
        # файла, получил бы чужой поток-владелец.
        from main.post_startup_import_warmup import AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES

        source_root = Path(__file__).resolve().parents[1] / "src"
        instance_at_module_level = re.compile(
            r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:Q[A-Z][A-Za-z]+|[A-Za-z_.]*Manager|[A-Za-z_.]*Bridge)\(",
            flags=re.MULTILINE,
        )
        for name in AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES:
            path = source_root.joinpath(*name.split(".")).with_suffix(".py")
            if not path.exists():
                continue
            with self.subTest(module=name):
                self.assertIsNone(instance_at_module_level.search(path.read_text(encoding="utf-8")))

    def test_failing_import_does_not_stop_the_rest(self) -> None:
        warmed = entry.warm_up_modules(("zapret_no_such_module_xyz", "asyncio"))

        self.assertEqual(warmed, ("asyncio",))
        self.assertIn("asyncio", sys.modules)

    def test_warmup_runs_in_background_daemon_thread(self) -> None:
        started: list[threading.Thread] = []
        real_thread = threading.Thread

        def _record(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            started.append(thread)
            return thread

        with patch("threading.Thread", side_effect=_record):
            entry.start_qtawesome_warmup()

        self.assertEqual(len(started), 1)
        thread = started[0]
        self.assertTrue(thread.daemon)
        self.assertEqual(thread.name, "import-warmup")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_warmup_thread_imports_every_listed_module(self) -> None:
        with patch.object(entry, "IMPORT_WARMUP_MODULES", ("json", "base64")):
            entry.start_qtawesome_warmup()
            for thread in threading.enumerate():
                if thread.name == "import-warmup":
                    thread.join(timeout=10)

        self.assertIn("json", sys.modules)
        self.assertIn("base64", sys.modules)

    def test_qtawesome_policy_keeps_only_used_prefixes(self) -> None:
        module = SimpleNamespace(
            _BUNDLED_FONTS=(
                ("fa5", "fa5.ttf", "fa5.json"),
                ("fa5s", "fa5s.ttf", "fa5s.json"),
                ("fa5b", "fa5b.ttf", "fa5b.json"),
                ("mdi6", "mdi6.ttf", "mdi6.json"),
                ("ph", "ph.ttf", "ph.json"),
                ("ri", "ri.ttf", "ri.json"),
            )
        )

        selected = configure_qtawesome_module(module)

        self.assertEqual(selected, QT_AWESOME_ALLOWED_PREFIXES)
        self.assertEqual(
            tuple(bundle[0] for bundle in module._BUNDLED_FONTS),
            QT_AWESOME_ALLOWED_PREFIXES,
        )

    def test_qtawesome_policy_fails_when_required_font_is_missing(self) -> None:
        bundles = (
            ("fa5s", "fa5s.ttf", "fa5s.json"),
            ("mdi", "mdi.ttf", "mdi.json"),
        )

        with self.assertRaisesRegex(RuntimeError, "fa5b"):
            select_qtawesome_bundles(bundles)

    def test_qtawesome_warmup_error_is_reported_before_window_import(self) -> None:
        with patch.object(entry, "IMPORT_WARMUP_MODULES", ("qtawesome",)), patch(
            "main.qtawesome_font_policy.configure_qtawesome_module",
            side_effect=RuntimeError("broken font policy"),
        ):
            entry.start_qtawesome_warmup()
            with self.assertRaisesRegex(RuntimeError, "политику шрифтов"):
                entry.wait_for_qtawesome_warmup(timeout=10)

    def test_source_uses_only_allowed_qtawesome_prefixes(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src"
        pattern = re.compile(
            r"['\"]([a-z0-9]+)\.[A-Za-z0-9_-]+['\"]",
            flags=re.IGNORECASE,
        )
        known_qtawesome_prefixes = {
            "fa5",
            "fa5s",
            "fa5b",
            "fa6",
            "fa6s",
            "fa6b",
            "ei",
            "mdi",
            "mdi6",
            "ph",
            "ri",
            "msc",
        }
        used: set[str] = set()
        for path in source_root.rglob("*.py"):
            for prefix in pattern.findall(path.read_text(encoding="utf-8")):
                if prefix in known_qtawesome_prefixes:
                    used.add(prefix)

        self.assertTrue(used)
        self.assertEqual(used - set(QT_AWESOME_ALLOWED_PREFIXES), set())


if __name__ == "__main__":
    unittest.main()
