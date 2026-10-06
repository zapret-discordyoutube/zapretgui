from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from presets.ui.control.control_page_shared import ControlPageActionMixin


CONFLICT_STOP_TEXT = "Останавливаем подбор стратегии перед запуском Zapret..."


class _Page(ControlPageActionMixin):
    """Страница управления: только передаёт нажатие пульту и показывает ожидание."""

    def __init__(self, launch_control) -> None:
        self._launch_control = launch_control
        self.loading_calls: list[tuple[bool, str]] = []
        self._bind_launch_control()

    def set_loading(self, loading: bool, text: str = "") -> None:
        self.loading_calls.append((bool(loading), str(text or "")))


class ControlStartConflictStopTests(unittest.TestCase):
    """Ручной запуск ждёт, пока остановится конфликтующая проверка блокировок.

    Проверяется вся цепочка: нажатие на странице управления -> пульт окна
    (ui.launch_control) -> команда странице BlockCheck -> запуск Zapret.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from main.window_page_deps_setup import _build_window_launch_control

        self.window = QObject()
        self.runtime = SimpleNamespace(
            is_available=Mock(return_value=True),
            start=Mock(return_value=True),
            stop=Mock(return_value=True),
        )
        self.tray = SimpleNamespace(configure=Mock())
        self.status_calls: list[str] = []
        self.launch_control = _build_window_launch_control(
            self.window,
            features=SimpleNamespace(runtime=self.runtime, tray=self.tray),
            state=SimpleNamespace(
                ui=SimpleNamespace(
                    snapshot=lambda: SimpleNamespace(launch_phase="stopped", launch_running=False)
                )
            ),
            page_actions=SimpleNamespace(set_status=self.status_calls.append, request_exit=Mock()),
        )
        self.page = _Page(self.launch_control)
        self.scheduled: list[object] = []

    def _patched(self, blockcheck_answers):
        from ui import launch_control as launch_control_module

        return (
            patch.object(
                launch_control_module.QTimer,
                "singleShot",
                side_effect=lambda _delay_ms, callback: self.scheduled.append(callback),
            ),
            patch("ui.window_adapter.send_page_command", **blockcheck_answers),
        )

    def test_manual_start_waits_until_blockcheck_conflict_stops(self) -> None:
        from app.page_names import PageName

        timer, command = self._patched({"side_effect": [True, False]})
        with timer, command as send_page_command:
            self.page._start_dpi()

            self.runtime.start.assert_not_called()
            self.assertEqual(len(self.scheduled), 1)
            self.assertIn(CONFLICT_STOP_TEXT, self.status_calls)
            self.assertEqual(self.page.loading_calls, [(True, CONFLICT_STOP_TEXT)])

            self.scheduled.pop(0)()

        self.runtime.start.assert_called_once_with()
        self.assertEqual(self.scheduled, [])
        self.assertEqual(send_page_command.call_count, 2)
        send_page_command.assert_called_with(
            self.window,
            PageName.BLOCKCHECK,
            "stop_runtime_conflicting_checks",
            {"source": "dpi_start"},
            ensure=False,
        )
        self.assertEqual(self.page.loading_calls[-1], (False, ""))

    def test_manual_start_is_immediate_without_blockcheck_conflict(self) -> None:
        timer, command = self._patched({"return_value": False})
        with timer, command as send_page_command:
            self.page._start_dpi()

        self.runtime.start.assert_called_once_with()
        self.assertEqual(send_page_command.call_count, 1)
        self.assertEqual(self.scheduled, [])
        self.assertEqual(self.page.loading_calls, [])
        self.assertEqual(self.status_calls, [])

    def test_manual_start_gives_up_when_blockcheck_conflict_never_stops(self) -> None:
        from ui import launch_control as launch_control_module

        timer, command = self._patched({"return_value": True})
        with (
            timer,
            command,
            patch.object(launch_control_module, "RUNTIME_START_CONFLICT_STOP_MAX_RETRIES", 3),
        ):
            self.page._start_dpi()
            while self.scheduled:
                self.scheduled.pop(0)()

        self.runtime.start.assert_not_called()
        self.assertEqual(self.page.loading_calls[-1], (False, ""))
        self.assertIn("Подбор стратегии ещё останавливается", self.status_calls[-1])

    def test_tray_gets_the_same_launch_control_as_the_page(self) -> None:
        self.tray.configure.assert_called_once_with(launch_control=self.launch_control)


if __name__ == "__main__":
    unittest.main()
