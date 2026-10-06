"""Точный таймер Windows включён, только пока окно программы на экране."""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from ui import precise_timer  # noqa: E402
from ui.precise_timer import WindowPreciseTimer, set_precise_timer  # noqa: E402


_APP = QApplication.instance() or QApplication([])


class SetPreciseTimerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        self._saved = precise_timer._precise_timer_on
        precise_timer._precise_timer_on = False
        self.addCleanup(setattr, precise_timer, "_precise_timer_on", self._saved)

    def _begin(self) -> bool:
        self.calls.append("begin")
        return True

    def _end(self) -> bool:
        self.calls.append("end")
        return True

    def test_begin_and_end_are_paired(self) -> None:
        # У Windows каждому timeBeginPeriod нужен ровно один timeEndPeriod:
        # повторное включение без пары оставило бы точный таймер навсегда.
        for enabled in (True, True, True, False, False, True, False):
            set_precise_timer(enabled, begin=self._begin, end=self._end)

        self.assertEqual(self.calls, ["begin", "end", "begin", "end"])
        self.assertFalse(precise_timer.is_precise_timer_on())

    def test_refused_request_is_not_counted_as_enabled(self) -> None:
        state = set_precise_timer(True, begin=lambda: False, end=self._end)

        self.assertFalse(state)
        set_precise_timer(False, begin=self._begin, end=self._end)
        self.assertEqual(self.calls, [])

    def test_other_systems_do_nothing(self) -> None:
        if sys.platform == "win32":
            self.skipTest("проверка для систем без winmm")
        self.assertFalse(set_precise_timer(True))


class WindowPreciseTimerTests(unittest.TestCase):
    def _window(self) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        return window

    def test_hidden_window_turns_precise_timer_off(self) -> None:
        states: list[bool] = []
        window = self._window()

        watcher = WindowPreciseTimer(window, apply=states.append)

        # Запуск в трей: окна нет на экране, точный таймер не нужен.
        self.assertEqual(states, [False])
        self.assertIsNotNone(watcher)

    def test_timer_follows_show_minimize_and_hide(self) -> None:
        states: list[bool] = []
        window = self._window()
        watcher = WindowPreciseTimer(window, apply=states.append)
        states.clear()

        window.show()
        _APP.processEvents()
        self.assertTrue(states and states[-1] is True)

        window.setWindowState(Qt.WindowState.WindowMinimized)
        _APP.processEvents()
        self.assertIs(states[-1], False)

        window.setWindowState(Qt.WindowState.WindowNoState)
        _APP.processEvents()
        self.assertIs(states[-1], True)

        window.hide()
        _APP.processEvents()
        self.assertIs(states[-1], False)
        self.assertIsNotNone(watcher)


class PreciseTimerWiringTests(unittest.TestCase):
    def test_startup_turns_it_on_before_the_window_exists(self) -> None:
        from main import qt_runtime

        source = inspect.getsource(qt_runtime.ensure_qt_runtime)

        # На запуск приходится больше всего фоновой работы, а без точного
        # таймера интервал переключения GIL в 1 мс на Windows не действует.
        self.assertIn("set_precise_timer(True)", source)
        self.assertLess(source.index("apply_gui_gil_switch_interval()"), source.index("set_precise_timer(True)"))

    def test_window_visibility_owns_it_after_startup(self) -> None:
        from main import entry

        source = inspect.getsource(entry._finish_event_loop_bootstrap)

        self.assertIn("install_window_precise_timer(window)", source)


if __name__ == "__main__":
    unittest.main()
