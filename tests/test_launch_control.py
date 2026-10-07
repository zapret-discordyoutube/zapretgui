from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication


def _store(phase: str, running: bool = False):
    return SimpleNamespace(snapshot=lambda: SimpleNamespace(launch_phase=phase, launch_running=running))


def _runtime(*, available: bool = True):
    return SimpleNamespace(
        start=Mock(return_value=True),
        stop=Mock(return_value=True),
        restart=Mock(return_value=True),
        is_available=Mock(return_value=available),
    )


class LaunchControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _control(self, phase: str, *, runtime=None, **kwargs):
        from ui.launch_control import LaunchControl

        runtime = runtime or _runtime()
        kwargs.setdefault("request_exit", Mock())
        return LaunchControl(runtime_feature=runtime, ui_state_store=_store(phase), **kwargs), runtime

    def test_toggle_starts_or_stops_by_phase(self) -> None:
        cases = {
            "stopped": "start",
            "failed": "start",
            "running": "stop",
            "starting": "stop",
            "autostart_pending": "stop",
            "stopping": "",
        }
        for phase, expected in cases.items():
            with self.subTest(phase=phase):
                control, runtime = self._control(phase)

                self.assertEqual(control.toggle(), expected)
                self.assertEqual(runtime.start.call_count, 1 if expected == "start" else 0)
                self.assertEqual(runtime.stop.call_count, 1 if expected == "stop" else 0)

    def test_unknown_phase_falls_back_to_running_flag(self) -> None:
        from ui.launch_control import LaunchControl

        runtime = _runtime()
        store = SimpleNamespace(snapshot=lambda: SimpleNamespace(launch_phase="", launch_running=True))
        control = LaunchControl(runtime_feature=runtime, ui_state_store=store, request_exit=Mock())

        self.assertEqual(control.phase(), "running")
        self.assertEqual(control.toggle(), "stop")

    def test_start_waits_for_conflicting_checks_before_runtime_start(self) -> None:
        from ui import launch_control as module

        conflicts = iter([True, True, False])
        control, runtime = self._control("stopped", stop_conflicting_checks=lambda: next(conflicts))
        scheduled: list[object] = []

        with patch.object(module.QTimer, "singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)):
            control.start()
            self.assertTrue(control.is_preparing())
            self.assertIn("подбор стратегии", control.preparing_text())
            runtime.start.assert_not_called()
            scheduled.pop(0)()
            scheduled.pop(0)()

        runtime.start.assert_called_once_with()
        self.assertFalse(control.is_preparing())

    def test_toggle_is_ignored_while_preparing_and_stop_cancels_waiting(self) -> None:
        from ui import launch_control as module

        control, runtime = self._control("stopped", runtime=_runtime(available=False))
        preparing: list[tuple[bool, str]] = []
        control.preparingChanged.connect(lambda active, text: preparing.append((active, text)))
        scheduled: list[object] = []

        with patch.object(module.QTimer, "singleShot", side_effect=lambda _ms, callback: scheduled.append(callback)):
            control.start()
            self.assertEqual(control.toggle(), "")
            control.stop()
            scheduled.pop(0)()

        runtime.start.assert_not_called()
        runtime.stop.assert_called_once_with()
        self.assertEqual(preparing, [(True, "Подготовка запуска..."), (False, "")])

    def test_restart_starts_when_not_running(self) -> None:
        control, runtime = self._control("stopped")
        control.restart()
        runtime.start.assert_called_once_with()
        runtime.restart.assert_not_called()

        control, runtime = self._control("running")
        control.restart()
        runtime.restart.assert_called_once_with()
        runtime.start.assert_not_called()

    def test_stop_and_exit_goes_only_through_exit_owner(self) -> None:
        request_exit = Mock()
        control, runtime = self._control("running", request_exit=request_exit)

        control.stop_and_exit()

        # Сам LaunchControl ни DPI не останавливает, ни программу не закрывает:
        # это делает владелец выхода (ApplicationLifecycle).
        request_exit.assert_called_once_with(stop_dpi=True)
        runtime.stop.assert_not_called()

    def test_phase_color_matches_status_card_colors(self) -> None:
        from presets.ui.control import control_runtime
        from ui.launch_control import phase_color

        for phase in ("running", "starting", "stopping", "failed"):
            with self.subTest(phase=phase):
                plan = control_runtime.build_status_plan(state=phase, last_error="", language="ru")
                self.assertEqual(phase_color(phase), plan.dot_color)
        self.assertIsNone(phase_color("stopped"))

    def test_control_page_mixin_delegates_to_launch_control(self) -> None:
        from presets.ui.control.control_page_shared import ControlPageActionMixin

        class Page(ControlPageActionMixin):
            def __init__(self) -> None:
                self._launch_control = Mock()
                self.loading: list[tuple[bool, str]] = []

            def set_loading(self, loading: bool, text: str = "") -> None:
                self.loading.append((loading, text))

        page = Page()
        page._toggle_dpi()
        page._start_dpi()
        page._stop_dpi()
        page._stop_and_exit()
        page._on_launch_preparing_changed(True, "Подготовка запуска...")

        page._launch_control.toggle.assert_called_once_with()
        page._launch_control.start.assert_called_once_with()
        page._launch_control.stop.assert_called_once_with()
        page._launch_control.stop_and_exit.assert_called_once_with()
        self.assertEqual(page.loading, [(True, "Подготовка запуска...")])


if __name__ == "__main__":
    unittest.main()
