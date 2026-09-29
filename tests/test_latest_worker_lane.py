from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ui.latest_worker_lane import LatestWorkerLane


class _Worker(QThread):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, payload, gate: threading.Event, fail: bool = False) -> None:
        super().__init__()
        self._request_id = request_id
        self._payload = payload
        self._gate = gate
        self._fail = fail

    def run(self) -> None:
        self._gate.wait(5)
        if self._fail:
            self.failed.emit(self._request_id, f"boom {self._payload}")
            return
        self.completed.emit(self._request_id, f"result {self._payload}")


def _spin(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)


class LatestWorkerLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _lane(self, *, fail: bool = False):
        gate = threading.Event()
        created: list = []
        results: list = []
        errors: list = []

        def create(request_id, payload):
            worker = _Worker(request_id, payload, gate, fail=fail)
            created.append(payload)
            return worker

        lane = LatestWorkerLane(
            name="test_lane",
            create_worker=create,
            on_result=lambda payload, result: results.append((payload, result)),
            on_error=lambda payload, error: errors.append((payload, error)),
        )
        return lane, gate, created, results, errors

    def test_requests_during_running_task_keep_only_the_latest(self) -> None:
        lane, gate, created, results, _errors = self._lane()

        lane.request(1)
        _spin(lambda: lane.runtime.is_running())
        lane.request(2)
        lane.request(3)
        self.assertEqual(created, [1])
        gate.set()
        _spin(lambda: results)
        _spin(lambda: not lane.is_busy())

        # Результат первой задачи устарел (ждал новый запрос) и не отдан.
        self.assertEqual(created, [1, 3])
        self.assertEqual(results, [(3, "result 3")])

    def test_errors_reach_page_with_their_payload(self) -> None:
        lane, gate, _created, results, errors = self._lane(fail=True)
        gate.set()

        lane.request("x")
        _spin(lambda: errors)

        self.assertEqual(errors, [("x", "boom x")])
        self.assertEqual(results, [])

    def test_closed_lane_drops_results_and_new_requests(self) -> None:
        lane, gate, created, results, _errors = self._lane()

        lane.request(1)
        _spin(lambda: lane.runtime.is_running())
        lane.close()
        lane.request(2)
        gate.set()
        _spin(lambda: False, timeout=0.2)

        self.assertEqual(created, [1])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
