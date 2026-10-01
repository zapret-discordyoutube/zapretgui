"""Правила runtime-слоя: когда останавливаются процессы и когда выгружается драйвер.

Нижний слой (winws_runtime.engine) проверен отдельно; здесь проверяется, что
раннеры и runtime-слой обращаются к нему в нужные моменты:

- остановка честно сообщает, вышел ли процесс;
- останавливаются только свои процессы winws;
- драйвер выгружается при окончательной остановке и не трогается при
  перезапуске и смене пресета;
- после сбоя запуска восстановление идёт в правильном порядке.
"""

from __future__ import annotations

import inspect
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from winws_runtime.engine import driver, winapi


def _make_runner(process=None):
    from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

    runner = object.__new__(Winws2StrategyRunner)
    runner.running_process = process
    runner.current_launch_label = "Preset"
    runner.current_strategy_args = ["@config.txt"]
    runner._preset_file_path = "preset.txt"
    runner._last_applied_base_launch_args = ()
    runner._set_runner_state_locked = Mock()
    runner._stop_own_engine_processes = Mock(return_value=True)
    runner._release_windivert_driver = Mock()
    return runner


def _alive_process(pid: int = 4321):
    process = Mock()
    process.pid = pid
    process.poll.return_value = None
    return process


class RunnerStopTests(unittest.TestCase):
    def test_stop_reports_success_only_when_exit_is_confirmed(self) -> None:
        from winws_runtime.runners import runner_base

        runner = _make_runner(_alive_process())
        with patch.object(runner_base, "stop_process", return_value=True) as stop:
            self.assertTrue(runner.stop(cleanup_services=False))

        stop.assert_called_once()
        self.assertIsNone(runner.running_process)

    def test_stop_reports_failure_when_process_does_not_exit(self) -> None:
        # Прежняя остановка возвращала True всегда — программа не знала,
        # что процесс застрял.
        from winws_runtime.runners import runner_base

        runner = _make_runner(_alive_process())
        with patch.object(runner_base, "stop_process", return_value=False):
            self.assertFalse(runner.stop(cleanup_services=False))

    def test_stop_reports_failure_when_leftover_engine_does_not_exit(self) -> None:
        runner = _make_runner(None)
        runner._stop_own_engine_processes.return_value = False

        self.assertFalse(runner.stop(cleanup_services=False))

    def test_final_stop_releases_the_driver(self) -> None:
        from winws_runtime.runners import runner_base

        runner = _make_runner(_alive_process())
        with patch.object(runner_base, "stop_process", return_value=True):
            runner.stop(cleanup_services=True)

        runner._release_windivert_driver.assert_called_once_with()

    def test_stop_inside_restart_keeps_the_driver_loaded(self) -> None:
        # Между запусками драйвер остаётся загруженным: следующий winws
        # открывает его напрямую, не обращаясь к диспетчеру служб.
        from winws_runtime.runners import runner_base

        runner = _make_runner(_alive_process())
        with patch.object(runner_base, "stop_process", return_value=True):
            runner.stop(cleanup_services=False)

        runner._release_windivert_driver.assert_not_called()

    def test_driver_is_released_after_own_engines_are_stopped(self) -> None:
        from winws_runtime.runners import runner_base

        order: list[str] = []
        runner = _make_runner(_alive_process())
        runner._stop_own_engine_processes.side_effect = lambda: order.append("engines") or True
        runner._release_windivert_driver.side_effect = lambda: order.append("driver")
        with patch.object(runner_base, "stop_process", side_effect=lambda *_a, **_k: order.append("process") or True):
            runner.stop(cleanup_services=True)

        self.assertEqual(order, ["process", "engines", "driver"])

    def test_stuck_driver_is_reported_but_does_not_fail_the_stop(self) -> None:
        from winws_runtime.runners import runner_base
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = _make_runner(None)
        runner._release_windivert_driver = Winws2StrategyRunner._release_windivert_driver.__get__(runner)
        stuck = driver.DriverReleaseResult(
            driver.RELEASE_STUCK_ENTRY,
            service="Monkey",
            message=driver.MESSAGE_STUCK_ENTRY.format(name="Monkey"),
        )
        messages: list[tuple[str, str]] = []
        with (
            patch.object(runner_base, "release_windivert_driver_runtime", return_value=stuck),
            patch.object(runner_base, "log", lambda message, level="INFO": messages.append((level, message))),
        ):
            self.assertTrue(runner.stop(cleanup_services=True))

        self.assertIn(("WARNING", stuck.message), messages)

    def test_runner_stop_does_not_kill_engines_by_name(self) -> None:
        from winws_runtime.runners import runner_base, zapret1_runner, zapret2_runner

        for module in (runner_base, zapret1_runner, zapret2_runner):
            source = inspect.getsource(module)
            self.assertNotIn("kill_winws", source, module.__name__)
            self.assertNotIn("force_kill_all_winws_processes", source, module.__name__)

    def test_runners_have_no_blind_pauses(self) -> None:
        from winws_runtime.runners import preset_runner_support, runner_base, zapret1_runner, zapret2_runner

        for module in (runner_base, zapret1_runner, zapret2_runner, preset_runner_support):
            self.assertNotIn("time.sleep", inspect.getsource(module), module.__name__)


class RuntimeApiTests(unittest.TestCase):
    def _api(self):
        from winws_runtime.runtime.runtime_api import PresetLaunchRuntimeApi

        return PresetLaunchRuntimeApi(r"C:\Zapret\Dev\exe\winws2.exe")

    def test_foreign_engine_is_not_a_residual_process(self) -> None:
        # Чужой winws программа не останавливает, поэтому и остатком он не
        # является: иначе остановка вечно докладывала бы «не удалось».
        from winws_runtime.runtime import runtime_api

        with patch.object(runtime_api, "get_canonical_winws_process_pids", return_value={}):
            self.assertFalse(self._api().has_residual_processes(silent=True))

        with patch.object(
            runtime_api, "get_canonical_winws_process_pids", return_value={"winws2.exe": [4321]}
        ):
            self.assertTrue(self._api().has_residual_processes(silent=True))

    def test_residual_check_does_not_fall_back_to_process_name(self) -> None:
        from winws_runtime.runtime import runtime_api

        self.assertFalse(hasattr(runtime_api, "has_any_winws_process"))

    def test_stop_all_processes_stops_only_own_engines_without_pause(self) -> None:
        from winws_runtime.runtime import runtime_api

        with patch.object(runtime_api, "stop_own_winws_processes_runtime", return_value=True) as stop_own:
            self.assertTrue(self._api().stop_all_processes())
        stop_own.assert_called_once_with()

        with patch.object(runtime_api, "stop_own_winws_processes_runtime", return_value=False):
            self.assertFalse(self._api().stop_all_processes())

        self.assertNotIn("time.sleep", inspect.getsource(runtime_api))

    def test_final_cleanup_releases_the_driver(self) -> None:
        from winws_runtime.runtime import runtime_api

        released = driver.DriverReleaseResult(driver.RELEASE_RELEASED, service="Monkey")
        with patch.object(runtime_api, "release_windivert_driver_runtime", return_value=released) as release:
            self.assertTrue(self._api().cleanup_windivert_service())
        release.assert_called_once_with()

    def test_final_cleanup_reports_a_stuck_driver(self) -> None:
        from winws_runtime.runtime import runtime_api

        stuck = driver.DriverReleaseResult(
            driver.RELEASE_STUCK_STOP_PENDING,
            service="Monkey",
            message=driver.MESSAGE_STOP_PENDING.format(name="Monkey"),
        )
        with patch.object(runtime_api, "release_windivert_driver_runtime", return_value=stuck):
            self.assertFalse(self._api().cleanup_windivert_service())

    def test_driver_left_in_use_is_not_a_cleanup_failure(self) -> None:
        from winws_runtime.runtime import runtime_api

        skipped = driver.DriverReleaseResult(driver.RELEASE_SKIPPED_IN_USE, service="Monkey")
        with patch.object(runtime_api, "release_windivert_driver_runtime", return_value=skipped):
            self.assertTrue(self._api().cleanup_windivert_service())


class SystemOpsTests(unittest.TestCase):
    def test_recovery_stops_engines_before_releasing_the_driver(self) -> None:
        # Пока жив хоть один winws, драйвер занят: останавливать его в этот
        # момент — значит получить зависшую службу.
        from winws_runtime.runtime import system_ops

        order: list[str] = []
        with (
            patch.object(
                system_ops,
                "stop_own_winws_processes_runtime",
                side_effect=lambda: order.append("engines") or True,
            ),
            patch.object(
                system_ops,
                "release_windivert_driver_runtime",
                side_effect=lambda: order.append("driver")
                or driver.DriverReleaseResult(driver.RELEASE_RELEASED, service="Monkey"),
            ),
            patch.object(
                system_ops,
                "ensure_windivert_driver_startable_runtime",
                side_effect=lambda: order.append("check") or driver.DriverPreflight(ok=True),
            ),
        ):
            self.assertTrue(system_ops.recover_windivert_runtime())

        self.assertEqual(order, ["engines", "driver", "check"])

    def test_recovery_fails_when_the_driver_stays_stuck(self) -> None:
        from winws_runtime.runtime import system_ops

        blocked = driver.DriverPreflight(
            ok=False,
            blocker=driver.BLOCKER_STUCK_ENTRY,
            service="Monkey",
            message=driver.MESSAGE_STUCK_ENTRY.format(name="Monkey"),
        )
        with (
            patch.object(system_ops, "stop_own_winws_processes_runtime", return_value=True),
            patch.object(
                system_ops,
                "release_windivert_driver_runtime",
                return_value=driver.DriverReleaseResult(driver.RELEASE_STUCK_ENTRY, service="Monkey"),
            ),
            patch.object(system_ops, "ensure_windivert_driver_startable_runtime", return_value=blocked),
        ):
            self.assertFalse(system_ops.recover_windivert_runtime())

    def test_any_engine_by_name_keeps_the_driver_in_use(self) -> None:
        # Драйвером пользуется любой winws, не только наш.
        from winws_runtime.runtime import system_ops

        with patch.object(system_ops, "list_engine_processes", return_value=[(77, "winws.exe")]):
            self.assertTrue(system_ops._any_winws_process_alive_strict())
        with patch.object(system_ops, "list_engine_processes", return_value=[]):
            self.assertFalse(system_ops._any_winws_process_alive_strict())

    def test_own_engine_stop_reports_failed_snapshot_as_failure(self) -> None:
        from winws_runtime.engine.process_control import EngineStopResult
        from winws_runtime.runtime import system_ops

        with (
            patch.object(system_ops, "own_engine_exe_paths", return_value=[r"C:\Zapret\Dev\exe\winws2.exe"]),
            patch.object(system_ops, "stop_engine_processes", return_value=EngineStopResult(snapshot_failed=True)),
        ):
            self.assertFalse(system_ops.stop_own_winws_processes_runtime())

    def test_old_cleanup_ladder_is_gone(self) -> None:
        from winws_runtime.runtime import system_ops

        for name in (
            "aggressive_windivert_cleanup_runtime",
            "standard_windivert_cleanup_runtime",
            "restore_known_windivert_services_demand_start_runtime",
            "clear_stopped_windivert_delete_flags_runtime",
            "stop_and_delete_runtime_services",
            "unload_known_windivert_drivers_runtime",
            "find_stale_windivert_delete_pending_services_runtime",
            "wait_for_windivert_spawn_ready_runtime",
            "force_kill_all_winws_processes",
        ):
            self.assertFalse(hasattr(system_ops, name), name)

    def test_runtime_layer_does_not_edit_service_registry(self) -> None:
        from winws_runtime.runtime import system_ops

        source = inspect.getsource(system_ops)
        self.assertNotIn("winreg", source)
        self.assertNotIn("DeleteFlag", source)


class UserStopReleasesDriverTests(unittest.TestCase):
    def test_user_stop_is_a_final_stop(self) -> None:
        # Кнопка «Стоп» — окончательная остановка: драйвер выгружается и его
        # служба исчезает из системы.
        from app.feature_facades.runtime import RuntimeFeature

        signature = inspect.signature(RuntimeFeature.stop)
        self.assertIs(signature.parameters["cleanup_services"].default, True)

    def test_restart_does_not_release_the_driver(self) -> None:
        from winws_runtime.runtime import restart_flow

        source = inspect.getsource(restart_flow)
        self.assertIn("cleanup_services=False", source)
        self.assertNotIn("cleanup_services=True", source)


class StopWorkerTests(unittest.TestCase):
    def _worker(self, *, running: bool, cleanup_services: bool):
        from winws_runtime.runtime.control_workers import PresetLaunchStopWorker

        runtime_api = Mock()
        runtime_api.has_residual_processes.return_value = running
        worker = PresetLaunchStopWorker(
            "zapret2_mode",
            runtime_feature=SimpleNamespace(),
            runtime_api=runtime_api,
            force_cleanup=False,
            cleanup_services=cleanup_services,
        )
        return worker, runtime_api

    def test_final_stop_releases_driver_even_when_engine_already_exited(self) -> None:
        # Движок мог упасть сам, а драйвер — остаться загруженным.
        worker, runtime_api = self._worker(running=False, cleanup_services=True)

        worker.run()

        runtime_api.cleanup_windivert_service.assert_called_once_with()

    def test_transition_stop_leaves_driver_when_engine_already_exited(self) -> None:
        worker, runtime_api = self._worker(running=False, cleanup_services=False)

        worker.run()

        runtime_api.cleanup_windivert_service.assert_not_called()

    def test_final_stop_of_running_engine_includes_driver_release(self) -> None:
        from winws_runtime.runtime import control_workers

        worker, _runtime_api = self._worker(running=True, cleanup_services=True)
        result = SimpleNamespace(still_running=False)
        with patch.object(control_workers, "shutdown_runtime_sync", return_value=result) as shutdown:
            worker.run()

        self.assertTrue(shutdown.call_args.kwargs["include_cleanup"])
        self.assertTrue(shutdown.call_args.kwargs["cleanup_services"])


class StuckDriverDiagnosisTests(unittest.TestCase):
    def _find(self, services: dict):
        from winws_runtime.health import winws_exit_diagnosis

        with patch.object(winapi, "query_service", side_effect=lambda name: services.get(name)):
            return winws_exit_diagnosis._find_stuck_windivert_driver_service()

    def test_running_disabled_driver_is_not_a_problem(self) -> None:
        # У работающего драйвера запись «отключена и помечена на удаление» —
        # так его помечает сам WinDivert. Прежняя диагностика считала это
        # поломкой и предлагала «аварийную очистку».
        running = winapi.ServiceInfo("Monkey", winapi.SERVICE_RUNNING, winapi.SERVICE_DISABLED, "")

        self.assertIsNone(self._find({"Monkey": running}))

    def test_stopped_disabled_entry_is_reported_as_stuck(self) -> None:
        stuck = winapi.ServiceInfo("Monkey", winapi.SERVICE_STOPPED, winapi.SERVICE_DISABLED, "")

        self.assertEqual(self._find({"Monkey": stuck}), "Monkey")

    def test_absent_service_is_not_a_problem(self) -> None:
        self.assertIsNone(self._find({}))

    def test_scm_failure_is_not_reported_as_stuck(self) -> None:
        from winws_runtime.health import winws_exit_diagnosis

        with patch.object(winapi, "query_service", side_effect=winapi.WinApiError("OpenSCManagerW", 5)):
            self.assertIsNone(winws_exit_diagnosis._find_stuck_windivert_driver_service())

    def test_stuck_entry_cause_names_the_real_reason(self) -> None:
        from winws_runtime.health import winws_exit_diagnosis

        with (
            patch.object(winws_exit_diagnosis, "_check_windivert_files", return_value=[]),
            patch.object(winws_exit_diagnosis, "_check_bfe_service", return_value=True),
            patch.object(winws_exit_diagnosis, "_find_stuck_windivert_driver_service", return_value="Monkey"),
        ):
            cause, solution, auto_fix = winws_exit_diagnosis._probe_service_disabled_cause()

        self.assertIn("Monkey", cause)
        self.assertIn("держит", cause)
        self.assertIn("перезагрузите", solution)
        # Автолечение тут бессильно: запись держит чужая программа.
        self.assertIsNone(auto_fix)


class Winws1StartConfirmationTests(unittest.TestCase):
    def test_winws1_output_goes_to_a_file_and_confirms_the_start(self) -> None:
        # Раньше вывод winws выбрасывался целиком, и разбор причины отказа по
        # тексту для него не работал.
        import tempfile

        from winws_runtime.engine.startup import REASON_READY, EngineStartOutcome
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        with tempfile.TemporaryDirectory() as tmp:
            runner = object.__new__(Winws1StrategyRunner)
            runner.winws_exe = "winws.exe"
            runner.work_dir = tmp
            runner.running_process = None
            runner.current_launch_label = None
            runner.current_strategy_args = None
            runner._last_spawn_exit_code = None
            runner._last_spawn_stderr = ""
            runner._last_startup_output_path = ""
            runner._set_runner_state_locked = Mock()
            runner._create_startup_info = Mock(return_value=None)
            runner._set_last_error = Mock()
            runner._start_process_exit_watcher = Mock()

            artifact = SimpleNamespace(launch_args=("@config.txt",), preset_path="preset.txt")
            fake_process = SimpleNamespace(pid=1357, returncode=None)

            with (
                patch(
                    "winws_runtime.runners.zapret1_runner.subprocess.Popen",
                    return_value=fake_process,
                ) as popen,
                patch(
                    "winws_runtime.runners.zapret1_runner.wait_engine_ready",
                    return_value=EngineStartOutcome(True, REASON_READY, True, 0.04),
                ) as wait_ready,
            ):
                self.assertTrue(runner._spawn_process_locked(artifact, "Preset"))

            kwargs = popen.call_args.kwargs
            self.assertIsNot(kwargs["stdout"], subprocess.DEVNULL)
            self.assertIsNot(kwargs["stdout"], subprocess.PIPE)
            self.assertIs(kwargs["stdout"], kwargs["stderr"])
            self.assertEqual(wait_ready.call_args.args[1], kwargs["stdout"].name)
            self.assertEqual(runner._last_startup_output_path, kwargs["stdout"].name)
            runner._start_process_exit_watcher.assert_called_once_with(fake_process)

    def test_winws1_failed_start_reads_reason_from_output_file(self) -> None:
        import tempfile

        from winws_runtime.engine.startup import REASON_EXITED, EngineStartOutcome
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        with tempfile.TemporaryDirectory() as tmp:
            runner = object.__new__(Winws1StrategyRunner)
            runner.winws_exe = "winws.exe"
            runner.work_dir = tmp
            runner.running_process = None
            runner.current_launch_label = None
            runner.current_strategy_args = None
            runner._last_spawn_exit_code = None
            runner._last_spawn_stderr = ""
            runner._last_startup_output_path = ""
            runner._set_runner_state_locked = Mock()
            runner._create_startup_info = Mock(return_value=None)
            runner._set_last_error = Mock()

            artifact = SimpleNamespace(launch_args=("@config.txt",), preset_path="preset.txt")

            def fake_popen(*_args, **kwargs):
                kwargs["stderr"].write(b"windivert: error opening filter: Access is denied.\n")
                kwargs["stderr"].flush()
                return SimpleNamespace(pid=1357, returncode=5, poll=lambda: 5)

            with (
                patch("winws_runtime.runners.zapret1_runner.subprocess.Popen", side_effect=fake_popen),
                patch(
                    "winws_runtime.runners.zapret1_runner.wait_engine_ready",
                    return_value=EngineStartOutcome(False, REASON_EXITED, False, 0.03),
                ),
            ):
                self.assertFalse(runner._spawn_process_locked(artifact, "Preset"))

            self.assertEqual(runner._last_spawn_exit_code, 5)
            self.assertIn("access is denied", runner._last_spawn_stderr.lower())


if __name__ == "__main__":
    unittest.main()
