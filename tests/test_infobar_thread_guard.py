"""Плашки InfoBar создаются только в потоке окна.

Создание элемента окна из фонового потока на Windows останавливает программу
целиком (Dev 21.1.7.19). Страж стоит в единственной точке, через которую
проходит создание любой плашки, и переносит чужой вызов в поток окна.
"""

from __future__ import annotations

import inspect
import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui import infobar_thread_guard as guard_module
from ui.infobar_thread_guard import install_infobar_window_thread_guard, reset_reported_call_sites


def _fake_info_bar_class():
    """Заменитель InfoBar: запоминает, в каком потоке и с чем его создали."""

    class FakeInfoBar:
        created: list[tuple[int, tuple, dict]] = []

        @classmethod
        def new(cls, *args, **kwargs):
            cls.created.append((threading.get_ident(), args, kwargs))
            return ("bar", len(cls.created))

        @classmethod
        def info(cls, title, content, **kwargs):
            # Как в qfluentwidgets: фабрики идут через new.
            return cls.new("info", title, content, **kwargs)

    FakeInfoBar.created = []
    return FakeInfoBar


class InfoBarWindowThreadGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        reset_reported_call_sites()
        self.info_bar = _fake_info_bar_class()
        install_infobar_window_thread_guard(self.info_bar)

    def _from_background(self, callback):
        result: list = []

        def _run() -> None:
            result.append(callback())

        worker = threading.Thread(target=_run, name="presets-queue")
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "вызов из фонового потока не вернулся")
        return result[0]

    def test_window_thread_creates_the_bar_at_once(self) -> None:
        bar = self.info_bar.new("info", "Готово", "Текст", parent=None)

        self.assertEqual(bar, ("bar", 1))
        self.assertEqual(self.info_bar.created, [(threading.get_ident(), ("info", "Готово", "Текст"), {"parent": None})])

    def test_background_thread_never_creates_the_bar_itself(self) -> None:
        with patch.object(guard_module, "_log"):
            bar = self._from_background(lambda: self.info_bar.new("info", "Готово", "Текст", parent=None))

            # Фоновый поток плашку не создал и не получил: её ещё нет.
            self.assertIsNone(bar)
            self.assertEqual(self.info_bar.created, [])

            self._app.processEvents()

        self.assertEqual(
            self.info_bar.created,
            [(threading.get_ident(), ("info", "Готово", "Текст"), {"parent": None})],
        )

    def test_factories_are_guarded_because_they_go_through_new(self) -> None:
        with patch.object(guard_module, "_log"):
            self._from_background(lambda: self.info_bar.info("Готово", "Текст", duration=3000))
            self.assertEqual(self.info_bar.created, [])
            self._app.processEvents()

        self.assertEqual(len(self.info_bar.created), 1)
        self.assertEqual(self.info_bar.created[0][0], threading.get_ident())
        self.assertEqual(self.info_bar.created[0][2], {"duration": 3000})

    def test_offender_is_named_in_the_log_once_per_call_site(self) -> None:
        def _offender():
            return self.info_bar.new("info", "Готово", "Текст")

        with patch.object(guard_module, "_log") as log:
            self._from_background(_offender)
            self._from_background(_offender)
            self._app.processEvents()

        warnings = [call.args[0] for call in log.call_args_list if call.args[1] == "WARNING"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("InfoBar запрошен из фонового потока «presets-queue»", warnings[0])
        self.assertIn("WindowNotificationCenter.notify", warnings[0])
        # По стеку видно место вызова.
        self.assertIn("_offender", warnings[0])
        self.assertEqual(len(self.info_bar.created), 2)

    def test_error_while_showing_does_not_escape_into_the_event_loop(self) -> None:
        class Broken:
            @classmethod
            def new(cls, *args, **kwargs):
                raise RuntimeError("окно закрыто")

        install_infobar_window_thread_guard(Broken)

        with patch.object(guard_module, "_log") as log:
            self._from_background(lambda: Broken.new("info", "Готово", "Текст"))
            self._app.processEvents()

        self.assertTrue(any("показать не удалось" in call.args[0] for call in log.call_args_list))

    def test_guard_is_installed_once(self) -> None:
        guarded_new = self.info_bar.__dict__["new"]

        install_infobar_window_thread_guard(self.info_bar)

        self.assertIs(self.info_bar.__dict__["new"], guarded_new)

    def test_guard_is_the_outermost_infobar_hook_at_startup(self) -> None:
        """Проверка потока идёт раньше кода, который читает размеры окна-родителя."""
        from main import qt_runtime

        source = inspect.getsource(qt_runtime)
        self.assertLess(
            source.index("install_infobar_adaptive_layout()"),
            source.index("install_infobar_window_thread_guard()"),
        )
        self.assertLess(
            source.index("install_infobar_min_duration()"),
            source.index("install_infobar_window_thread_guard()"),
        )

    def test_real_infobar_factories_still_go_through_new(self) -> None:
        """Страж стоит на InfoBar.new: если библиотека перестанет ходить через него, тест скажет."""
        from qfluentwidgets import InfoBar

        for name in ("info", "success", "warning", "error"):
            with self.subTest(factory=name):
                original = getattr(InfoBar, f"_zapret_min_duration_original_{name}", None) or getattr(InfoBar, name)
                self.assertIn("cls.new(", inspect.getsource(getattr(original, "__func__", original)))


if __name__ == "__main__":
    unittest.main()
