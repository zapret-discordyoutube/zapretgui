"""Post-mortem diagnosis acceptance tests (.agent/tasks/postmortem-diagnosis/spec.md).

AC1: diagnose_unexpected_winws_exit builds cause-specific messages.
AC2: LaunchRuntimeService uses the injected diagnoser on the 3rd missed probe,
     falls back to the generic message, stays backward compatible without it.
AC3: runner post-mortem accessors.
AC4: resolve_unexpected_exit_message logs exactly one ERROR with data,
     stays silent without a runner/snapshot.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class DiagnoseUnexpectedExitTests(unittest.TestCase):
    def test_silent_exit_for_code_one_without_output(self) -> None:
        """Причина такого отказа не угадывается, а устанавливается пробами."""
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit
        from winws_runtime.health.silent_exit_probe import SilentExitReport

        with patch(
            "winws_runtime.health.post_mortem.probe_silent_exit",
            return_value=SilentExitReport(verified=("файлы программы на месте",)),
        ):
            result = diagnose_unexpected_winws_exit(1, "", exe_name="winws2.exe")

        self.assertEqual(result.kind, "silent_exit")
        self.assertIn("winws2.exe", result.message)
        self.assertIn("не выдав ни одного сообщения", result.message)
        self.assertIn("Причина не установлена", result.message)

    def test_crash_codes_are_reported_as_crash(self) -> None:
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit

        for code in (0xC0000005, 0xC0000409):
            result = diagnose_unexpected_winws_exit(code, "", exe_name="winws2.exe")
            self.assertEqual(result.kind, "crash", hex(code))
            self.assertIn("аварийное завершение", result.message)
            self.assertIn(f"0x{code:08X}", result.message)

    def test_windivert_output_uses_start_time_diagnosis(self) -> None:
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit

        with patch(
            "winws_runtime.health.process_health_check.diagnose_winws_exit",
            return_value=SimpleNamespace(
                cause="Драйвер WinDivert выгружен",
                solution="Перезапустите Zapret",
            ),
        ):
            result = diagnose_unexpected_winws_exit(
                34,
                "windivert: error opening filter: The service cannot be started",
                exe_name="winws2.exe",
            )

        self.assertEqual(result.kind, "diagnosed")
        self.assertIn("winws2.exe неожиданно завершился", result.message)
        self.assertIn("Драйвер WinDivert выгружен", result.message)
        self.assertIn("Перезапустите Zapret", result.message)

    def test_transient_dll_init_kind_is_classified(self) -> None:
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit

        result = diagnose_unexpected_winws_exit(0xC0000142, "", exe_name="winws2.exe")
        self.assertEqual(result.kind, "transient_dll_init")
        self.assertIn("инициализировать DLL", result.message)

    def test_generic_fallback_contains_code_and_relevant_line(self) -> None:
        from winws_runtime.health.post_mortem import diagnose_unexpected_winws_exit

        result = diagnose_unexpected_winws_exit(
            7,
            "line one\nsome error happened here\n",
            exe_name="winws.exe",
        )
        self.assertEqual(result.kind, "unknown")
        self.assertIn("(код 7)", result.message)
        self.assertIn("some error happened here", result.message)
        self.assertIn("winws.exe", result.message)


class LaunchRuntimeServiceDiagnoserTests(unittest.TestCase):
    def _make_service(self, diagnoser):
        from app.state_store import AppUiState, MainWindowStateStore
        from winws_runtime.state import LaunchRuntimeService

        store = MainWindowStateStore(AppUiState())
        if diagnoser is None:
            service = LaunchRuntimeService(store)
        else:
            service = LaunchRuntimeService(store, unexpected_exit_diagnoser=diagnoser)
        service.begin_start(launch_method="zapret2_mode", expected_process="winws2.exe")
        service.mark_running(pid=4242, expected_process="winws2.exe")
        return service

    def _drive_process_loss(self, service) -> None:
        with patch(
            "winws_runtime.state.launch_runtime_service.is_winws_process_pid_alive",
            return_value=False,
        ):
            for _probe in range(3):
                service.observe_process_details({})

    def test_third_missed_probe_uses_diagnoser_message(self) -> None:
        service = self._make_service(lambda: "winws2.exe неожиданно завершился: тест")

        self._drive_process_loss(service)

        snapshot = service.snapshot()
        self.assertEqual(snapshot.phase, "failed")
        self.assertEqual(snapshot.last_error, "winws2.exe неожиданно завершился: тест")

    def test_empty_diagnoser_result_falls_back_to_generic_message(self) -> None:
        service = self._make_service(lambda: "")

        self._drive_process_loss(service)

        snapshot = service.snapshot()
        self.assertEqual(snapshot.phase, "failed")
        self.assertEqual(snapshot.last_error, "winws2.exe не найден среди активных процессов")

    def test_raising_diagnoser_falls_back_to_generic_message(self) -> None:
        def _boom() -> str:
            raise RuntimeError("diagnosis exploded")

        service = self._make_service(_boom)

        self._drive_process_loss(service)

        snapshot = service.snapshot()
        self.assertEqual(snapshot.phase, "failed")
        self.assertEqual(snapshot.last_error, "winws2.exe не найден среди активных процессов")

    def test_service_without_diagnoser_keeps_previous_behavior(self) -> None:
        service = self._make_service(None)

        self._drive_process_loss(service)

        snapshot = service.snapshot()
        self.assertEqual(snapshot.phase, "failed")
        self.assertEqual(snapshot.last_error, "winws2.exe не найден среди активных процессов")

    def test_diagnoser_not_called_before_third_miss(self) -> None:
        calls: list[int] = []

        def _diagnoser() -> str:
            calls.append(1)
            return "причина"

        service = self._make_service(_diagnoser)
        with patch(
            "winws_runtime.state.launch_runtime_service.is_winws_process_pid_alive",
            return_value=False,
        ):
            service.observe_process_details({})
            service.observe_process_details({})

        self.assertEqual(calls, [])
        self.assertEqual(service.snapshot().phase, "running")


class RunnerPostMortemAccessorTests(unittest.TestCase):
    def test_zapret2_reads_remembered_startup_output_file(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = object.__new__(Winws2StrategyRunner)
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "winws2_startup_x.log"
            output_path.write_text("boot ok\nwindivert: driver gone\n", encoding="utf-8")
            runner._last_startup_output_path = str(output_path)

            self.assertIn("windivert: driver gone", runner.read_post_mortem_output())

        runner._last_startup_output_path = ""
        self.assertEqual(runner.read_post_mortem_output(), "")

    def test_zapret1_has_no_post_mortem_output(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        runner = object.__new__(Winws1StrategyRunner)
        self.assertEqual(runner.read_post_mortem_output(), "")

    def test_snapshot_none_for_absent_or_alive_process(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        runner = object.__new__(Winws1StrategyRunner)
        runner.running_process = None
        self.assertIsNone(runner.build_post_mortem_snapshot())

        runner.running_process = SimpleNamespace(poll=lambda: None)
        runner.current_launch_label = "Preset"
        self.assertIsNone(runner.build_post_mortem_snapshot())

    def test_snapshot_for_dead_process_contains_exit_facts(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = object.__new__(Winws2StrategyRunner)
        runner.running_process = SimpleNamespace(poll=lambda: 1)
        runner.current_launch_label = "Default v1"
        runner._last_startup_output_path = ""

        snapshot = runner.build_post_mortem_snapshot()

        self.assertEqual(snapshot["exit_code"], 1)
        self.assertEqual(snapshot["strategy_name"], "Default v1")
        self.assertEqual(snapshot["output"], "")


class ResolveUnexpectedExitMessageTests(unittest.TestCase):
    class _LogRecorder:
        def __init__(self) -> None:
            self.records: list[tuple[str, str]] = []

        def __call__(self, message, level="INFO", *args, **kwargs) -> None:
            self.records.append((str(message), str(level)))

        def error_messages(self) -> list[str]:
            return [message for message, level in self.records if level == "ERROR"]

    def test_returns_message_and_logs_single_error_for_dead_process(self) -> None:
        from winws_runtime.health.post_mortem import resolve_unexpected_exit_message

        runner = SimpleNamespace(
            winws_exe=r"G:\zapret\exe\winws2.exe",
            build_post_mortem_snapshot=lambda: {
                "exit_code": 1,
                "output": "",
                "strategy_name": "Default v1",
            },
        )
        recorder = self._LogRecorder()
        from winws_runtime.health.silent_exit_probe import SilentExitReport

        with (
            patch("winws_runtime.runners.runner_factory.get_current_runner", return_value=runner),
            patch("winws_runtime.health.post_mortem.log", recorder),
            patch(
                "winws_runtime.health.post_mortem.probe_silent_exit",
                return_value=SilentExitReport(verified=("файлы программы на месте",)),
            ) as probe,
        ):
            message = resolve_unexpected_exit_message()

        self.assertIn("winws2.exe", message)
        self.assertIn("не выдав ни одного сообщения", message)
        self.assertEqual(probe.call_args.kwargs["exe_path"], r"G:\zapret\exe\winws2.exe")
        self.assertEqual(recorder.error_messages(), [message])

    def test_returns_empty_and_silent_without_runner(self) -> None:
        from winws_runtime.health.post_mortem import resolve_unexpected_exit_message

        recorder = self._LogRecorder()
        with (
            patch("winws_runtime.runners.runner_factory.get_current_runner", return_value=None),
            patch("winws_runtime.health.post_mortem.log", recorder),
        ):
            message = resolve_unexpected_exit_message()

        self.assertEqual(message, "")
        self.assertEqual(recorder.error_messages(), [])

    def test_returns_empty_and_silent_for_alive_process(self) -> None:
        from winws_runtime.health.post_mortem import resolve_unexpected_exit_message

        runner = SimpleNamespace(
            winws_exe="winws2.exe",
            build_post_mortem_snapshot=lambda: None,
        )
        recorder = self._LogRecorder()
        with (
            patch("winws_runtime.runners.runner_factory.get_current_runner", return_value=runner),
            patch("winws_runtime.health.post_mortem.log", recorder),
        ):
            message = resolve_unexpected_exit_message()

        self.assertEqual(message, "")
        self.assertEqual(recorder.error_messages(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
