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

    def test_asyncio_is_warmed_up(self) -> None:
        # Первое обращение к Telegram Proxy тянет asyncio вместе с
        # asyncio.windows_events — в логе это давало рывок на ~64 мс.
        self.assertIn("asyncio", entry.IMPORT_WARMUP_MODULES)

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
