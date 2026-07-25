"""Exit-watch / foreign winws / auto-restart acceptance tests
(.agent/tasks/exit-watch-autorestart/spec.md).

AC1: exit watcher fires for our dead current process, suppresses on identity
     mismatch and intentional-stop states, starts only on confirmed spawn.
AC3/AC4: main-thread handler outcomes and the auto-restart budget.
AC5: foreign winws classification and the warn-once observer.
AC6: scan guard TTL + blockcheck wiring contract.
AC7: structured post-mortem resolution (transient flag, scan-guard silence).
"""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class _LogRecorder:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def __call__(self, message, level="INFO", *args, **kwargs) -> None:
        self.records.append((str(message), str(level)))

    def messages(self, level: str) -> list[str]:
        return [message for message, lvl in self.records if lvl == level]


def _make_base_runner():
    from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

    runner = object.__new__(Winws1StrategyRunner)
    runner.running_process = None
    runner.current_launch_label = None
    runner._unexpected_process_exit_callback = None
    return runner


class ExitWatcherTests(unittest.TestCase):
    def test_callback_fires_for_current_dead_process_in_running_state(self) -> None:
        from winws_runtime.runners.preset_runner_support import PresetRunnerState

        runner = _make_base_runner()
        process = SimpleNamespace(poll=lambda: 1)
        runner.running_process = process
        runner.get_runner_state_snapshot = lambda: SimpleNamespace(state=PresetRunnerState.RUNNING)
        callback = Mock()
        runner._unexpected_process_exit_callback = callback

        runner._on_watched_process_exit(process)

        callback.assert_called_once_with()

    def test_suppressed_when_process_was_replaced(self) -> None:
        runner = _make_base_runner()
        old_process = SimpleNamespace(poll=lambda: 1)
        runner.running_process = SimpleNamespace(poll=lambda: None)  # new process
        callback = Mock()
        runner._unexpected_process_exit_callback = callback

        runner._on_watched_process_exit(old_process)

        callback.assert_not_called()

    def test_suppressed_during_intentional_stop_states(self) -> None:
        from winws_runtime.runners.preset_runner_support import PresetRunnerState

        for state in (PresetRunnerState.STOPPING, PresetRunnerState.IDLE):
            runner = _make_base_runner()
            process = SimpleNamespace(poll=lambda: 1)
            runner.running_process = process
            runner.get_runner_state_snapshot = lambda s=state: SimpleNamespace(state=s)
            callback = Mock()
            runner._unexpected_process_exit_callback = callback

            runner._on_watched_process_exit(process)

            callback.assert_not_called()

    def test_watcher_thread_invokes_exit_handler_after_wait(self) -> None:
        runner = _make_base_runner()
        fired = threading.Event()
        runner._on_watched_process_exit = lambda _process: fired.set()
        process = SimpleNamespace(wait=lambda: 0)

        runner._start_process_exit_watcher(process)

        self.assertTrue(fired.wait(timeout=2.0))

    def test_zapret2_spawn_success_starts_watcher(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        with tempfile.TemporaryDirectory() as tmp:
            runner = object.__new__(Winws2StrategyRunner)
            runner.winws_exe = "winws2.exe"
            runner.work_dir = tmp
            runner.running_process = None
            runner.current_launch_label = None
            runner.current_strategy_args = None
            runner._last_spawn_exit_code = None
            runner._last_spawn_stderr = ""
            runner._last_startup_output_path = ""
            runner._prepare_state_for_spawn_locked = Mock()
            runner._set_runner_state_locked = Mock()
            runner._log_winws2_launch_command = Mock()
            runner._create_startup_info = Mock(return_value=None)
            runner._set_last_error = Mock()
            runner._run_preset_dry_run_locked = Mock(return_value=True)
            runner._start_process_exit_watcher = Mock()

            artifact = SimpleNamespace(
                launch_args=("@config.txt",),
                preset_path="preset.txt",
                normalized_text="--wf-tcp-out=443\n",
            )
            fake_process = SimpleNamespace(pid=1234, returncode=None)

            with (
                patch("winws_runtime.runners.zapret2_runner.subprocess.Popen", return_value=fake_process),
                patch("winws_runtime.runners.zapret2_runner.wait_for_process_stable_start", return_value=True),
                patch("winws_runtime.runners.zapret2_runner.time.sleep"),
            ):
                self.assertTrue(
                    runner._spawn_process_locked(artifact, "Preset", preset_switch=False)
                )

            runner._start_process_exit_watcher.assert_called_once_with(fake_process)


class UnexpectedExitHandlerTests(unittest.TestCase):
    def _make_events(self, *, phase: str = "running", command_port=None):
        from app.feature_facades.runtime_parts import RuntimeEvents

        runtime_service = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(phase=phase),
            mark_start_failed=Mock(),
        )
        return RuntimeEvents(runtime_service=runtime_service, command_port=command_port)

    def _resolution(self, *, transient: bool):
        from winws_runtime.health.post_mortem import PostMortemResolution

        return PostMortemResolution(
            message="winws2.exe неожиданно завершился: тестовая причина",
            kind="transient_dll_init" if transient else "external_kill",
            transient=transient,
            exit_code=0xC0000142 if transient else 1,
        )

    def test_no_action_when_resolution_is_none(self) -> None:
        events = self._make_events()
        recorder = _LogRecorder()
        with patch("log.log.log", recorder):
            events.handle_unexpected_process_exit(None)

        events.runtime_service.mark_start_failed.assert_not_called()
        self.assertEqual(recorder.messages("ERROR"), [])

    def test_no_action_when_phase_is_not_active(self) -> None:
        events = self._make_events(phase="stopped")
        events.handle_unexpected_process_exit(self._resolution(transient=False))
        events.runtime_service.mark_start_failed.assert_not_called()

    def test_non_transient_failure_publishes_single_error(self) -> None:
        events = self._make_events()
        recorder = _LogRecorder()
        resolution = self._resolution(transient=False)
        with patch("log.log.log", recorder):
            events.handle_unexpected_process_exit(resolution)

        self.assertEqual(recorder.messages("ERROR"), [resolution.message])
        events.runtime_service.mark_start_failed.assert_called_once_with(resolution.message)

    def test_transient_failure_triggers_auto_restart(self) -> None:
        command_port = SimpleNamespace(start=Mock(return_value=True))
        events = self._make_events(command_port=command_port)
        recorder = _LogRecorder()
        resolution = self._resolution(transient=True)
        with patch("log.log.log", recorder):
            events.handle_unexpected_process_exit(resolution)

        command_port.start.assert_called_once_with()
        self.assertEqual(recorder.messages("ERROR"), [])
        self.assertTrue(
            any("автоматический перезапуск" in msg.lower() for msg in recorder.messages("WARNING"))
        )
        events.runtime_service.mark_start_failed.assert_called_once_with(resolution.message)

    def test_exhausted_budget_disables_auto_restart(self) -> None:
        command_port = SimpleNamespace(start=Mock(return_value=True))
        events = self._make_events(command_port=command_port)
        now = time.monotonic()
        events.auto_restart_history = [now - 1.0, now - 2.0]
        recorder = _LogRecorder()
        resolution = self._resolution(transient=True)
        with patch("log.log.log", recorder):
            events.handle_unexpected_process_exit(resolution)

        command_port.start.assert_not_called()
        errors = recorder.messages("ERROR")
        self.assertEqual(len(errors), 1)
        self.assertIn("Автоперезапуск отключён", errors[0])
        events.runtime_service.mark_start_failed.assert_called_once()

    def test_auto_restart_budget_window_slides(self) -> None:
        events = self._make_events()
        self.assertTrue(events._try_reserve_auto_restart(now=1000.0))
        self.assertTrue(events._try_reserve_auto_restart(now=1001.0))
        self.assertFalse(events._try_reserve_auto_restart(now=1002.0))
        self.assertTrue(events._try_reserve_auto_restart(now=1000.0 + 601.0 + 1.0))


class ForeignWinwsDetectionTests(unittest.TestCase):
    def test_find_foreign_classifies_by_path(self) -> None:
        from winws_runtime.runtime import process_probe

        entries = [(100, "winws2.exe"), (200, "winws.exe"), (300, "winws2.exe")]
        paths = {
            100: r"c:\zapret\exe\winws2.exe",  # canonical
            200: r"c:\other\winws.exe",       # foreign
            300: "",                            # unqueryable → skipped
        }
        with (
            patch.object(process_probe, "_iter_winws_process_entries", return_value=entries),
            patch.object(process_probe, "_query_process_image_path", side_effect=lambda pid: paths[pid]),
            patch.object(
                process_probe,
                "get_expected_winws_paths",
                return_value={
                    "winws2.exe": r"c:\zapret\exe\winws2.exe",
                    "winws.exe": r"c:\zapret\exe\winws.exe",
                },
            ),
        ):
            foreign = process_probe.find_foreign_winws_processes()

        self.assertEqual([record.pid for record in foreign], [200])
        self.assertEqual(foreign[0].exe_path, r"c:\other\winws.exe")

    def _make_objects(self, *, running: bool = True):
        from app.feature_facades.runtime_parts import RuntimeObjects

        runtime_service = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(running=running),
        )
        return RuntimeObjects(runtime_service=runtime_service)

    def test_warns_once_per_foreign_pid_set_while_running(self) -> None:
        objects = self._make_objects(running=True)
        recorder = _LogRecorder()
        foreign = {123: r"c:\other\winws.exe"}
        with (
            patch("log.log.log", recorder),
            patch(
                "winws_runtime.runtime.scan_guard.is_external_winws_scan_active",
                return_value=False,
            ),
        ):
            objects.observe_foreign_processes(foreign)
            objects.observe_foreign_processes(foreign)  # same set → no repeat
            objects.observe_foreign_processes({})       # cleared → reset
            objects.observe_foreign_processes(foreign)  # returns → warn again

        errors = recorder.messages("ERROR")
        self.assertEqual(len(errors), 2)
        self.assertIn("посторонний winws", errors[0])
        self.assertIn("PID 123", errors[0])

    def test_silent_when_dpi_not_running(self) -> None:
        objects = self._make_objects(running=False)
        recorder = _LogRecorder()
        with patch("log.log.log", recorder):
            objects.observe_foreign_processes({123: r"c:\other\winws.exe"})
        self.assertEqual(recorder.messages("ERROR"), [])

    def test_silent_while_external_scan_active(self) -> None:
        objects = self._make_objects(running=True)
        recorder = _LogRecorder()
        with (
            patch("log.log.log", recorder),
            patch(
                "winws_runtime.runtime.scan_guard.is_external_winws_scan_active",
                return_value=True,
            ),
        ):
            objects.observe_foreign_processes({123: r"c:\other\winws.exe"})
        self.assertEqual(recorder.messages("ERROR"), [])


class ScanGuardTests(unittest.TestCase):
    def test_flag_lifecycle_and_ttl(self) -> None:
        from winws_runtime.runtime import scan_guard

        fake_now = [1000.0]
        with patch.object(scan_guard.time, "monotonic", side_effect=lambda: fake_now[0]):
            scan_guard.mark_external_winws_scan_active(True, ttl_seconds=10.0)
            self.assertTrue(scan_guard.is_external_winws_scan_active())

            fake_now[0] = 1011.0  # TTL expired — a dead scanner cannot mute us forever
            self.assertFalse(scan_guard.is_external_winws_scan_active())

            fake_now[0] = 1012.0
            scan_guard.mark_external_winws_scan_active(True, ttl_seconds=10.0)
            self.assertTrue(scan_guard.is_external_winws_scan_active())
            scan_guard.mark_external_winws_scan_active(False)
            self.assertFalse(scan_guard.is_external_winws_scan_active())

    def test_blockcheck_scanner_wraps_run_with_scan_flag(self) -> None:
        from blockcheck.strategy_scanner import StrategyScanner

        source = inspect.getsource(StrategyScanner.run)
        self.assertIn("mark_external_winws_scan_active(True)", source)
        self.assertIn("finally", source)
        self.assertIn("mark_external_winws_scan_active(False)", source)


class ResolveUnexpectedExitTests(unittest.TestCase):
    def _fake_runner(self, exit_code: int, output: str = ""):
        return SimpleNamespace(
            winws_exe=r"g:\zapret\exe\winws2.exe",
            build_post_mortem_snapshot=lambda: {
                "exit_code": exit_code,
                "output": output,
                "strategy_name": "Default v1",
            },
        )

    def test_transient_for_dll_init_failure(self) -> None:
        from winws_runtime.health.post_mortem import resolve_unexpected_exit

        with (
            patch(
                "winws_runtime.runners.runner_factory.get_current_runner",
                return_value=self._fake_runner(0xC0000142),
            ),
            patch(
                "winws_runtime.runtime.scan_guard.is_external_winws_scan_active",
                return_value=False,
            ),
        ):
            resolution = resolve_unexpected_exit()

        self.assertIsNotNone(resolution)
        self.assertTrue(resolution.transient)
        self.assertEqual(resolution.exit_code, 0xC0000142)

    def test_not_transient_for_external_kill_and_crash(self) -> None:
        from winws_runtime.health.post_mortem import resolve_unexpected_exit

        for exit_code in (1, 0xC0000005):
            with (
                patch(
                    "winws_runtime.runners.runner_factory.get_current_runner",
                    return_value=self._fake_runner(exit_code),
                ),
                patch(
                    "winws_runtime.runtime.scan_guard.is_external_winws_scan_active",
                    return_value=False,
                ),
            ):
                resolution = resolve_unexpected_exit()

            self.assertIsNotNone(resolution, hex(exit_code))
            self.assertFalse(resolution.transient, hex(exit_code))

    def test_none_while_external_scan_active(self) -> None:
        from winws_runtime.health.post_mortem import resolve_unexpected_exit

        with (
            patch(
                "winws_runtime.runtime.scan_guard.is_external_winws_scan_active",
                return_value=True,
            ),
            patch("winws_runtime.runners.runner_factory.get_current_runner") as get_runner,
        ):
            self.assertIsNone(resolve_unexpected_exit())
        get_runner.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
