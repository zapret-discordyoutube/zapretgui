import inspect
import sys
import unittest
from unittest.mock import Mock, patch


class LaunchConflictingProcessesTests(unittest.TestCase):
    def test_start_preflight_does_not_warn_just_because_process_hacker_is_running(self) -> None:
        from winws_runtime.runtime import conflict_flow

        runtime_owner = Mock()

        with patch.object(conflict_flow, "check_conflicting_processes") as check_conflicts:
            allowed = conflict_flow.handle_conflicting_processes_before_start(runtime_owner)

        self.assertTrue(allowed)
        check_conflicts.assert_not_called()

    def test_conflict_registry_lives_in_separate_module(self) -> None:
        from winws_runtime.health import launch_conflicts
        from winws_runtime.health import process_health_check

        self.assertTrue(callable(launch_conflicts.check_conflicting_processes))
        self.assertTrue(callable(launch_conflicts.build_launch_conflict_advice))
        self.assertNotIn("CONFLICTING_PROCESSES", inspect.getsource(process_health_check))

    @unittest.skipUnless(sys.platform == "win32", "проверка прав администратора использует WinAPI")
    def test_process_hacker_advice_is_added_after_windivert_launch_failure(self) -> None:
        import ctypes

        from winws_runtime.health import launch_conflicts
        from winws_runtime.health.process_health_check import diagnose_winws_exit

        conflict = {
            "exe": "processhacker.exe",
            "name": "Process Hacker",
            "reason": "мешает WinDivert",
            "solution": "Закройте Process Hacker",
            "pid": 4242,
        }

        with (
            # The access-denied handler first probes real admin rights and
            # short-circuits with generic advice when not elevated, so the
            # probe must be pinned for the conflict branch to be reachable.
            patch.object(ctypes.windll.shell32, "IsUserAnAdmin", return_value=1),
            patch.object(launch_conflicts, "check_conflicting_processes", return_value=[conflict]),
        ):
            diagnosis = diagnose_winws_exit(5, "Error opening filter: Access is denied")

        self.assertIsNotNone(diagnosis)
        assert diagnosis is not None
        self.assertIn("Process Hacker", diagnosis.cause)
        self.assertIn("помешал запуску Zapret", diagnosis.cause)
        self.assertIn("Закройте Process Hacker", diagnosis.solution)


if __name__ == "__main__":
    unittest.main()
