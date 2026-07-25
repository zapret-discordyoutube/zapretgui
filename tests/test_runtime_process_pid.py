import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.feature_facades.runtime_parts import RuntimeObjects
from winws_runtime.runtime.lifecycle_feedback import _start_worker_result
from winws_runtime.runtime.status_flow import is_running


class _RuntimeService:
    def __init__(
        self,
        *,
        launch_method: str = "zapret2_mode",
        phase: str = "stopped",
        running: bool = False,
        pid: int | None = None,
    ):
        self._snapshot = SimpleNamespace(
            launch_method=launch_method,
            phase=phase,
            running=running,
            pid=pid,
        )
        self.observed_details: list[dict] = []

    def snapshot(self):
        return self._snapshot

    def observe_process_details(self, details):
        normalized = dict(details or {})
        self.observed_details.append(normalized)
        winws2_pids = normalized.get("winws2.exe") or []
        if winws2_pids and isinstance(winws2_pids[0], int):
            self._snapshot = SimpleNamespace(
                launch_method="zapret2_mode",
                phase="running",
                running=True,
                pid=winws2_pids[0],
            )


class RuntimeProcessPidTests(unittest.TestCase):
    def test_current_process_pid_reads_existing_snapshot(self) -> None:
        runtime_service = _RuntimeService(
            launch_method="zapret2_mode",
            phase="running",
            running=True,
            pid=3333,
        )
        objects = RuntimeObjects(runtime_service=runtime_service)

        pid = objects.current_process_pid("zapret2_mode")

        self.assertEqual(pid, 3333)

    def test_current_process_pid_rejects_snapshot_for_other_launch_method(self) -> None:
        runtime_service = _RuntimeService(
            launch_method="zapret1_mode",
            phase="running",
            running=True,
            pid=1111,
        )
        objects = RuntimeObjects(runtime_service=runtime_service)

        pid = objects.current_process_pid("zapret2_mode")

        self.assertIsNone(pid)

    def test_start_worker_result_reads_prepared_pid_and_warnings(self) -> None:
        runtime_owner = SimpleNamespace(
            _dpi_start_worker=SimpleNamespace(
                started_pid=4444,
                warnings=["warning"],
            )
        )

        self.assertEqual(_start_worker_result(runtime_owner), (4444, ["warning"]))

    def test_is_running_uses_only_runtime_snapshot(self) -> None:
        runtime_api = Mock()
        runtime_owner = SimpleNamespace(
            _runtime_service=lambda: SimpleNamespace(
                snapshot=lambda: SimpleNamespace(phase="stopped", running=False)
            ),
            _runtime_api=lambda: runtime_api,
        )

        self.assertFalse(is_running(runtime_owner))
        runtime_api.is_any_running.assert_not_called()


if __name__ == "__main__":
    unittest.main()
