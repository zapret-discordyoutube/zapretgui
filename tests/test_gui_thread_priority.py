"""Окно получает замок Python у фоновых потоков без ожидания, пока оно на экране.

Замер на win10, сборка страницы в GUI-потоке рядом с одним занятым фоновым
потоком: интервал переключения 1 мс — 2568 мс, интервал 0,5 мс — 92 мс.
Windows не ждёт меньше миллисекунды: интервал в 1 мс длится ~2 мс, а интервал
меньше 1 мс CPython округляет до нуля.
"""

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

from ui import gui_thread_priority  # noqa: E402
from ui.gui_thread_priority import (  # noqa: E402
    BACKGROUND_GIL_SWITCH_INTERVAL_SEC,
    GUI_GIL_SWITCH_INTERVAL_SEC,
    WindowGilPriority,
    set_gui_gil_priority,
)


_APP = QApplication.instance() or QApplication([])


class GuiGilSwitchIntervalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_interval = sys.getswitchinterval()
        self.addCleanup(sys.setswitchinterval, self._original_interval)

    def test_gui_interval_is_below_one_millisecond(self) -> None:
        # Ровно 1 мс и больше возвращают ожидание в ~2 мс на каждое
        # обращение окна к своему коду.
        self.assertLess(GUI_GIL_SWITCH_INTERVAL_SEC, 0.001)
        self.assertGreater(GUI_GIL_SWITCH_INTERVAL_SEC, 0)

    def test_startup_lowers_the_interval(self) -> None:
        from main.qt_runtime import apply_gui_gil_switch_interval

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


class WindowGilPriorityTests(unittest.TestCase):
    def _window(self) -> QWidget:
        window = QWidget()
        self.addCleanup(window.deleteLater)
        return window

    def test_hidden_window_turns_priority_off(self) -> None:
        states: list[bool] = []
        window = self._window()

        watcher = WindowGilPriority(window, apply=states.append)

        # Запуск в трей: окна нет на экране.
        self.assertEqual(states, [False])
        self.assertIsNotNone(watcher)

    def test_priority_follows_show_minimize_and_hide(self) -> None:
        states: list[bool] = []
        window = self._window()
        watcher = WindowGilPriority(window, apply=states.append)
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


class GilPriorityWiringTests(unittest.TestCase):
    def test_startup_turns_it_on_before_the_window_exists(self) -> None:
        from main import qt_runtime

        # На запуск приходится больше всего фоновой работы.
        self.assertIn("apply_gui_gil_switch_interval()", inspect.getsource(qt_runtime.ensure_qt_runtime))

    def test_window_visibility_owns_it_after_startup(self) -> None:
        from main import entry

        source = inspect.getsource(entry._finish_event_loop_bootstrap)

        self.assertIn("install_window_gil_priority(window)", source)

    def test_program_does_not_change_the_system_clock_step(self) -> None:
        # Точный системный таймер Windows (timeBeginPeriod) был нужен только
        # при интервале в 1 мс. Анимациям он не нужен: Qt ведёт свои точные
        # таймеры сам (замер на win10: такт 33 мс — кадры через 33,0 мс и с
        # ним, и без него), а энергию он расходует.
        source_root = Path(gui_thread_priority.__file__).resolve().parents[1]
        users = sorted(
            str(path.relative_to(source_root))
            for path in source_root.rglob("*.py")
            if "timeBeginPeriod" in path.read_text(encoding="utf-8").replace("`timeBeginPeriod(1)`", "")
        )
        self.assertEqual(users, [])


if __name__ == "__main__":
    unittest.main()
