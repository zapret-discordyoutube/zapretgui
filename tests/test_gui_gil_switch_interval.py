from __future__ import annotations

import inspect
import sys
import unittest

from main.qt_runtime import apply_gui_gil_switch_interval, ensure_qt_runtime
from ui.precise_timer import (
    BACKGROUND_GIL_SWITCH_INTERVAL_SEC,
    GUI_GIL_SWITCH_INTERVAL_SEC,
    set_gui_gil_priority,
    set_window_on_screen,
)


class GuiGilSwitchIntervalTests(unittest.TestCase):
    """GUI-поток получает замок Python у фоновых потоков без ожидания.

    Замер на win10, сборка страницы в GUI-потоке рядом с одним занятым
    фоновым потоком: интервал 1 мс — 2568 мс, интервал 0,5 мс — 113 мс.
    Windows не ждёт меньше миллисекунды: интервал в 1 мс длится ~2 мс, а
    интервал меньше 1 мс CPython округляет до нуля.
    """

    def setUp(self) -> None:
        self._original_interval = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, self._original_interval)

    def test_gui_interval_is_below_one_millisecond(self) -> None:
        # Ровно 1 мс и больше возвращают ожидание в ~2 мс на каждое
        # обращение окна к своему коду.
        self.assertLess(GUI_GIL_SWITCH_INTERVAL_SEC, 0.001)
        self.assertGreater(GUI_GIL_SWITCH_INTERVAL_SEC, 0)

    def test_startup_lowers_the_interval(self) -> None:
        sys.setswitchinterval(0.005)

        apply_gui_gil_switch_interval()

        self.assertEqual(sys.getswitchinterval(), GUI_GIL_SWITCH_INTERVAL_SEC)

    def test_hidden_window_returns_python_default(self) -> None:
        # В трее рывков никто не видит, а ждущий замка поток при коротком
        # интервале не спит, а крутится.
        set_gui_gil_priority(True)
        set_gui_gil_priority(False)

        self.assertEqual(sys.getswitchinterval(), BACKGROUND_GIL_SWITCH_INTERVAL_SEC)
        self.assertEqual(BACKGROUND_GIL_SWITCH_INTERVAL_SEC, 0.005)

    def test_window_visibility_switches_the_interval(self) -> None:
        from unittest.mock import patch

        with patch("ui.precise_timer.set_precise_timer") as precise_timer:
            set_window_on_screen(True)
            self.assertEqual(sys.getswitchinterval(), GUI_GIL_SWITCH_INTERVAL_SEC)
            set_window_on_screen(False)
            self.assertEqual(sys.getswitchinterval(), BACKGROUND_GIL_SWITCH_INTERVAL_SEC)

        self.assertEqual([call.args[0] for call in precise_timer.call_args_list], [True, False])

    def test_qt_runtime_applies_the_interval(self) -> None:
        source = inspect.getsource(ensure_qt_runtime)

        self.assertIn("apply_gui_gil_switch_interval()", source)


if __name__ == "__main__":
    unittest.main()
