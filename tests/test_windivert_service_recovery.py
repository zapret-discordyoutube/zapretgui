import unittest
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch


class WinDivertServiceRecoveryTests(unittest.TestCase):
    def test_packaged_monkey_driver_is_accepted_as_windivert_file(self) -> None:
        from config.runtime_layout import ApplicationPaths
        from winws_runtime.health import winws_exit_diagnosis

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            exe_dir = root / "exe"
            exe_dir.mkdir()
            (exe_dir / "WinDivert.dll").write_bytes(b"dll")
            (exe_dir / "Monkey64.sys").write_bytes(b"driver")

            with patch.object(
                winws_exit_diagnosis,
                "APPLICATION_PATHS",
                ApplicationPaths.from_root(root),
            ):
                self.assertEqual(winws_exit_diagnosis._check_windivert_files(), [])

            (exe_dir / "Monkey64.sys").unlink()
            with patch.object(
                winws_exit_diagnosis,
                "APPLICATION_PATHS",
                ApplicationPaths.from_root(root),
            ):
                missing = winws_exit_diagnosis._check_windivert_files()

        self.assertEqual(
            missing,
            ["драйвер WinDivert (Monkey64.sys или WinDivert64.sys)"],
        )

    def test_detailed_diagnosis_explains_windows_and_process_codes(self) -> None:
        from winws_runtime.health.winws_exit_diagnosis import (
            WinDivertDiagnosis,
            format_winws_exit_diagnosis,
        )

        diagnosis = WinDivertDiagnosis(
            cause="Служба драйвера WinDivert (Monkey) отключена в системе",
            solution="Выполните аварийную очистку драйвера и повторите запуск",
            exit_code=34,
            win32_error=1058,
        )

        message = format_winws_exit_diagnosis(diagnosis, exe_name="winws2")

        self.assertIn("Найдена причина", message)
        self.assertIn("код ошибки Windows 1058", message)
        self.assertIn("код завершения процесса 34", message)
        self.assertIn("Что сделать", message)

    def test_recv_errno_5_exit_229_is_not_reported_as_windows_error_229(self) -> None:
        from winws_runtime.health.winws_exit_diagnosis import (
            diagnose_winws_exit,
            format_winws_exit_diagnosis,
        )

        output = "\n".join(
            (
                "github version v1.0.3 (b78b52c4) lua_compat_ver 6",
                "windivert initialized. capture is started.",
                "windivert: recv failed. errno 5",
            )
        )

        diagnosis = diagnose_winws_exit(229, output)

        self.assertIsNotNone(diagnosis)
        self.assertIsNone(diagnosis.win32_error)
        self.assertFalse(diagnosis.cause_is_exact)
        message = format_winws_exit_diagnosis(diagnosis, exe_name="winws2")
        self.assertIn("Что известно", message)
        self.assertIn("windivert: recv failed. errno 5", message)
        self.assertIn("код завершения процесса 229", message)
        self.assertIn("потерял исходный код Windows", message)
        self.assertIn("ERROR_IO_PENDING (997)", message)
        self.assertNotIn("код ошибки Windows 229", message)
        self.assertNotIn("Найдена причина", message)

    def test_empty_exit_34_is_treated_as_transient_windivert_1058(self) -> None:
        from winws_runtime.runners import runner_base

        class DummyRunner(runner_base.StrategyRunnerBase):
            def start_from_preset_file(self, preset_path: str, strategy_name: str = "Preset") -> bool:
                return True

            def switch_preset_file_fast(self, preset_path: str, strategy_name: str = "Preset", *, is_current=None) -> bool:
                return True

        with (
            patch.object(runner_base.os.path, "exists", return_value=True),
            patch(
                "winws_runtime.health.winws_exit_diagnosis._probe_service_disabled_cause",
                return_value=(
                    "WinDivert ещё не готов после предыдущего запуска или очистки",
                    "Повторите запуск после очистки",
                    None,
                ),
            ),
        ):
            runner = DummyRunner(r"C:\Zapret\Dev\exe\winws2.exe")
            retry = runner._should_retry_transient_windivert_service_error(
                "",
                34,
                retry_count=0,
                max_retry_count=1,
            )

        self.assertTrue(retry)

    def test_windivert_error_after_lua_header_is_reported_as_service_problem(self) -> None:
        from winws_runtime.health import process_health_check, winws_exit_diagnosis

        stderr = "\n".join(
            (
                "github version v1.0.1 lua_compat_ver 6",
                "Loading hostlist /lists/youtube.txt",
                "windivert: error opening filter: The service cannot be started, either because it is disabled or because it has no enabled devices associated with it.",
            )
        )

        with patch.object(
            winws_exit_diagnosis,
            "_probe_service_disabled_cause",
            return_value=("WinDivert service disabled", "Restore service", None),
        ):
            diagnosis = process_health_check.diagnose_winws_exit(87, stderr)

        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis.win32_error, 1058)
        self.assertEqual(diagnosis.cause, "WinDivert service disabled")

    def test_runner_runs_safe_windivert_autofix_once_after_failed_spawn(self) -> None:
        from winws_runtime.health.process_health_check import WinDivertDiagnosis
        from winws_runtime.runners import runner_base

        class DummyRunner(runner_base.StrategyRunnerBase):
            def start_from_preset_file(self, preset_path: str, strategy_name: str = "Preset") -> bool:
                return True

            def switch_preset_file_fast(self, preset_path: str, strategy_name: str = "Preset", *, is_current=None) -> bool:
                return True

        diagnosis = WinDivertDiagnosis(
            cause="Служба Base Filtering Engine (BFE) отключена",
            solution="Включите BFE",
            auto_fix="enable_bfe",
            exit_code=1068,
            win32_error=1068,
        )

        with (
            patch.object(runner_base.os.path, "exists", return_value=True),
            patch.object(runner_base, "diagnose_winws_exit", return_value=diagnosis),
            patch.object(runner_base, "execute_windivert_auto_fix", return_value=(True, "BFE запущена")) as auto_fix,
        ):
            runner = DummyRunner(r"C:\Zapret\Dev\exe\winws2.exe")
            recovered = runner._maybe_run_windivert_auto_fix_after_failed_spawn(
                "dependency service failed",
                1068,
                retry_count=0,
            )

        self.assertTrue(recovered)
        auto_fix.assert_called_once_with("enable_bfe")

    def test_runner_does_not_autofix_same_failure_twice(self) -> None:
        from winws_runtime.health.process_health_check import WinDivertDiagnosis
        from winws_runtime.runners import runner_base

        class DummyRunner(runner_base.StrategyRunnerBase):
            def start_from_preset_file(self, preset_path: str, strategy_name: str = "Preset") -> bool:
                return True

            def switch_preset_file_fast(self, preset_path: str, strategy_name: str = "Preset", *, is_current=None) -> bool:
                return True

        diagnosis = WinDivertDiagnosis(
            cause="Служба Base Filtering Engine (BFE) отключена",
            solution="Включите BFE",
            auto_fix="enable_bfe",
            exit_code=1068,
            win32_error=1068,
        )

        with (
            patch.object(runner_base.os.path, "exists", return_value=True),
            patch.object(runner_base, "diagnose_winws_exit", return_value=diagnosis),
            patch.object(runner_base, "execute_windivert_auto_fix", return_value=(True, "BFE запущена")) as auto_fix,
        ):
            runner = DummyRunner(r"C:\Zapret\Dev\exe\winws2.exe")
            recovered = runner._maybe_run_windivert_auto_fix_after_failed_spawn(
                "dependency service failed",
                1068,
                retry_count=1,
            )

        self.assertFalse(recovered)
        auto_fix.assert_not_called()

    def test_winws2_retries_after_successful_windivert_autofix(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = object.__new__(Winws2StrategyRunner)
        runner._last_spawn_exit_code = 1068
        runner._last_spawn_stderr = "dependency service failed"
        runner._should_retry_transient_windivert_service_error = Mock(return_value=False)
        runner._is_windivert_system_error = Mock(return_value=True)
        runner._is_windivert_conflict_error = Mock(return_value=False)
        runner._maybe_run_windivert_auto_fix_after_failed_spawn = Mock(return_value=True)
        runner._start_from_preset_file_locked = Mock(return_value=True)

        retried = runner._maybe_retry_after_failed_spawn_locked(
            "preset.txt",
            "Preset",
            cleanup_required=False,
            retry_count=0,
            stable_start_window_seconds=0.35,
        )

        self.assertTrue(retried)
        runner._maybe_run_windivert_auto_fix_after_failed_spawn.assert_called_once_with(
            "dependency service failed",
            1068,
            retry_count=0,
        )
        runner._start_from_preset_file_locked.assert_called_once_with(
            "preset.txt",
            "Preset",
            force_cleanup=True,
            retry_count=1,
            stable_start_window_seconds=0.35,
        )

    def test_winws1_retries_after_successful_windivert_autofix(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        runner = object.__new__(Winws1StrategyRunner)
        runner._last_spawn_exit_code = 1068
        runner._last_spawn_stderr = "dependency service failed"
        runner._should_retry_transient_windivert_service_error = Mock(return_value=False)
        runner._should_retry_unclassified_code_one = Mock(return_value=False)
        runner._is_windivert_system_error = Mock(return_value=True)
        runner._is_windivert_conflict_error = Mock(return_value=False)
        runner._maybe_run_windivert_auto_fix_after_failed_spawn = Mock(return_value=True)
        runner._start_from_preset_file_locked = Mock(return_value=True)

        retried = runner._maybe_retry_after_failed_spawn_locked(
            "preset.txt",
            "Preset",
            retry_count=0,
            max_retries=2,
            stable_start_window_seconds=0.35,
        )

        self.assertTrue(retried)
        runner._maybe_run_windivert_auto_fix_after_failed_spawn.assert_called_once_with(
            "dependency service failed",
            1068,
            retry_count=0,
        )
        runner._start_from_preset_file_locked.assert_called_once_with(
            "preset.txt",
            "Preset",
            retry_count=1,
            max_retries=2,
            stable_start_window_seconds=0.35,
        )

    def test_generic_service_disabled_does_not_blame_secure_boot_without_signature_error(self) -> None:
        from winws_runtime.health import process_health_check, winws_exit_diagnosis

        stderr = (
            "windivert: error opening filter: The service cannot be started, "
            "either because it is disabled or because it has no enabled devices associated with it."
        )

        with (
            patch.object(winws_exit_diagnosis, "_check_windivert_files", return_value=[]),
            patch.object(winws_exit_diagnosis, "_check_bfe_service", return_value=True),
            patch.object(winws_exit_diagnosis, "_check_secure_boot", return_value=True),
            patch.object(winws_exit_diagnosis, "_find_stuck_windivert_driver_service", return_value=None),
            patch.object(winws_exit_diagnosis, "_detect_active_antivirus", return_value=None),
            patch.object(winws_exit_diagnosis, "_check_network_adapters", return_value=True),
        ):
            diagnosis = process_health_check.diagnose_winws_exit(34, stderr)

        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis.win32_error, 1058)
        self.assertNotIn("Secure Boot блокирует", diagnosis.cause)
        self.assertIn("WinDivert не может запустить службу драйвера", diagnosis.cause)

    def test_lua_compat_mismatch_is_reported_as_version_mismatch(self) -> None:
        from winws_runtime.health import process_health_check

        stderr = "\n".join(
            (
                "github version v1.0.1 lua_compat_ver 6",
                "Error: LUA ERROR: /lua/zapret-lib.lua:4: Incompatible NFQWS2_COMPAT_VER. Use pktws and lua scripts from the same release !",
            )
        )

        diagnosis = process_health_check.diagnose_winws_exit(87, stderr)

        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis.cause, "winws2.exe и Lua-скрипты от разных версий")
        self.assertIn("Обновите папку lua", diagnosis.solution)

    def test_probe_uses_no_install_for_network_readiness(self) -> None:
        from winws_runtime.runtime import system_ops

        with (
            patch.object(system_ops, "_load_windivert_dll_runtime", return_value=object()),
            patch.object(
                system_ops,
                "_probe_windivert_open_runtime",
                side_effect=[(False, 1060), (False, 1060)],
            ) as open_probe,
        ):
            result = system_ops.probe_windivert_state_runtime()

        self.assertFalse(result.installed)
        self.assertFalse(result.ready)
        network_flags = open_probe.call_args_list[1].kwargs["flags"]
        self.assertTrue(network_flags & system_ops._WINDIVERT_FLAG_NO_INSTALL)

if __name__ == "__main__":
    unittest.main()
