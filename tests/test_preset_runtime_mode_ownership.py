from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from settings.mode import ORCHESTRA_MODE, ZAPRET2_MODE


class _RuntimeService:
    def __init__(self, launch_method: str, *, running: bool = True) -> None:
        self.launch_method = launch_method
        self.running = bool(running)
        self.busy_calls: list[tuple[bool, str]] = []

    def snapshot(self):
        return SimpleNamespace(
            launch_method=self.launch_method,
            running=self.running,
            phase="running" if self.running else "stopped",
        )

    def set_busy(self, busy: bool, text: str = "") -> bool:
        self.busy_calls.append((bool(busy), str(text or "")))
        return True


class _RuntimeOwner:
    def __init__(self, launch_method: str, *, running: bool = True) -> None:
        self.service = _RuntimeService(launch_method, running=running)
        self._presets_switch_thread = None
        self._presets_switch_worker = None
        self._presets_switch_requested_generation = 0
        self._presets_switch_completed_generation = 0
        self._presets_switch_method = ""
        self._presets_switch_debounce_timer = None
        self._presets_switch_debounce_method = ""
        self._dpi_start_thread = None
        self._dpi_stop_thread = None
        self._restart_request_generation = 0
        self._restart_completed_generation = 0
        self._restart_pending_stop_generation = 0
        self._restart_active_start_generation = 0
        self._restart_force_stop_generation = 0
        self._restart_target_launch_method = ""
        self.stop_calls: list[tuple[bool, bool]] = []
        self.start_calls: list[str | None] = []
        self.marked_stopped = 0

    def _runtime_service(self):
        return self.service

    def is_running(self) -> bool:
        return self.service.running

    def restart_dpi_async(
        self,
        *,
        force_full_stop: bool = False,
        target_launch_method: str | None = None,
    ) -> None:
        from winws_runtime.runtime.restart_flow import restart_dpi_async

        restart_dpi_async(
            self,
            force_full_stop=force_full_stop,
            target_launch_method=target_launch_method,
        )

    def stop_dpi_async(
        self,
        *,
        force_cleanup: bool = False,
        cleanup_services: bool = False,
    ) -> None:
        self.stop_calls.append((bool(force_cleanup), bool(cleanup_services)))

    def start_dpi_async(self, *, launch_method: str | None = None) -> None:
        self.start_calls.append(launch_method)

    def _mark_runtime_stopped(self) -> None:
        self.marked_stopped += 1
        self.service.running = False


class PresetRuntimeModeOwnershipTests(unittest.TestCase):
    def test_fast_switch_stays_inside_same_active_owner(self) -> None:
        from winws_runtime.runtime import restart_flow

        owner = _RuntimeOwner(ZAPRET2_MODE, running=True)

        with patch.object(restart_flow, "process_pending_presets_switch") as process_pending:
            restart_flow.switch_presets_async(owner, ZAPRET2_MODE)

        self.assertEqual(owner._presets_switch_requested_generation, 1)
        self.assertEqual(owner._presets_switch_method, ZAPRET2_MODE)
        self.assertEqual(owner.stop_calls, [])
        self.assertEqual(owner._restart_request_generation, 0)
        process_pending.assert_called_once_with(owner)

    def test_orchestra_to_preset_switch_uses_full_stop_then_target_mode_start(self) -> None:
        from winws_runtime.runtime.lifecycle_feedback import on_dpi_stop_finished
        from winws_runtime.runtime.restart_flow import switch_presets_async

        owner = _RuntimeOwner(ORCHESTRA_MODE, running=True)

        switch_presets_async(owner, ZAPRET2_MODE)

        self.assertEqual(owner._presets_switch_requested_generation, 0)
        self.assertEqual(owner._restart_target_launch_method, ZAPRET2_MODE)
        self.assertEqual(owner.stop_calls, [(True, False)])
        self.assertEqual(owner.start_calls, [])

        with patch("winws_runtime.runtime.lifecycle_feedback.set_runtime_owner_status"):
            on_dpi_stop_finished(owner, True, "")

        self.assertEqual(owner.marked_stopped, 1)
        self.assertEqual(owner.start_calls, [ZAPRET2_MODE])

    def test_owner_is_rechecked_after_delayed_switch_was_queued(self) -> None:
        from winws_runtime.runtime.restart_flow import process_pending_presets_switch

        owner = _RuntimeOwner(ORCHESTRA_MODE, running=True)
        owner._presets_switch_method = ZAPRET2_MODE
        owner._presets_switch_requested_generation = 3
        owner._presets_switch_completed_generation = 2

        with patch("winws_runtime.runtime.restart_flow.start_worker_thread") as start_worker:
            process_pending_presets_switch(owner)

        self.assertEqual(owner._presets_switch_completed_generation, 3)
        self.assertEqual(owner._restart_target_launch_method, ZAPRET2_MODE)
        self.assertEqual(owner.stop_calls, [(True, False)])
        start_worker.assert_not_called()

    def test_method_switch_plan_passes_target_mode_to_restart_owner(self) -> None:
        from winws_runtime.runtime.method_switch_flow import (
            MethodSwitchRuntimePlan,
            apply_method_switch_runtime_plan,
        )

        launch_runtime = SimpleNamespace(restart_dpi_async=Mock())
        runtime_service = SimpleNamespace(begin_stop=Mock(), set_busy=Mock())
        runtime_api = SimpleNamespace(set_expected_exe_path=Mock())
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(
                launch_runtime=launch_runtime,
                launch_runtime_api=runtime_api,
                runtime_service=runtime_service,
            )
        )
        plan = MethodSwitchRuntimePlan(
            method=ZAPRET2_MODE,
            expected_exe_path=r"C:\Zapret\Stable\exe\winws2.exe",
            expected_process_name="winws2.exe",
            autostart_enabled=True,
            can_autostart=True,
            dispatch_action="restart",
            requires_cleanup_stop=True,
        )

        with patch(
            "winws_runtime.runtime.method_switch_flow.QTimer.singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            apply_method_switch_runtime_plan(runtime_feature, plan)

        launch_runtime.restart_dpi_async.assert_called_once_with(
            force_full_stop=True,
            target_launch_method=ZAPRET2_MODE,
        )


if __name__ == "__main__":
    unittest.main()
