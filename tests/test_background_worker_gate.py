from __future__ import annotations

import unittest

from PyQt6.QtCore import QObject, pyqtSignal

from ui.background_worker_gate import (
    BackgroundWorkerGate,
    BackgroundWorkerTicket,
    background_worker_gate,
    reset_background_worker_gate,
)
from ui.one_shot_worker_runtime import OneShotWorkerRuntime


class _FakeWorker(QObject):
    """Минимальная замена QThread-воркера: старт и finished без реального потока."""

    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.deleted = False

    def start(self) -> None:
        self.started = True

    def isRunning(self) -> bool:  # noqa: N802 - Qt API
        return self.started

    def deleteLater(self) -> None:  # noqa: N802 - Qt API
        self.deleted = True

    def finish(self) -> None:
        self.started = False
        self.finished.emit()


class BackgroundWorkerGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = BackgroundWorkerGate(limit=2)
        self.started: list[str] = []

    def _submit(self, name: str) -> BackgroundWorkerTicket:
        ticket = BackgroundWorkerTicket()
        self.gate.submit(ticket, lambda: self.started.append(name))
        return ticket

    def test_limit_holds_extra_workers_in_queue(self) -> None:
        self._submit("a")
        self._submit("b")
        queued = self._submit("c")

        self.assertEqual(self.started, ["a", "b"])
        self.assertTrue(queued.is_pending())
        self.assertEqual(self.gate.active_count(), 2)
        self.assertEqual(self.gate.queued_count(), 1)

    def test_release_starts_next_worker_in_fifo_order(self) -> None:
        first = self._submit("a")
        self._submit("b")
        self._submit("c")
        self._submit("d")

        self.gate.release(first)

        self.assertEqual(self.started, ["a", "b", "c"])
        self.assertEqual(self.gate.queued_count(), 1)

    def test_cancel_removes_worker_before_start(self) -> None:
        self._submit("a")
        self._submit("b")
        queued = self._submit("c")

        cancelled_before_start = self.gate.cancel(queued)

        self.assertTrue(cancelled_before_start)
        self.assertEqual(self.gate.queued_count(), 0)
        self.assertTrue(queued.is_finished())
        self.assertNotIn("c", self.started)

    def test_cancel_of_active_worker_reports_it_already_started(self) -> None:
        active = self._submit("a")

        cancelled_before_start = self.gate.cancel(active)

        self.assertFalse(cancelled_before_start)
        self.assertEqual(self.gate.active_count(), 0)

    def test_long_running_worker_stops_blocking_the_queue(self) -> None:
        slow = self._submit("slow")
        self._submit("b")
        self._submit("c")

        self.gate._release_long_running(slow)

        self.assertEqual(self.started, ["slow", "b", "c"])
        self.assertEqual(self.gate.active_count(), 2)

    def test_release_after_long_running_release_is_harmless(self) -> None:
        slow = self._submit("slow")
        self.gate._release_long_running(slow)

        self.gate.release(slow)

        self.assertEqual(self.gate.active_count(), 0)
        self.assertEqual(self.gate.queued_count(), 0)

    def test_slot_is_reclaimed_when_worker_died_without_finished(self) -> None:
        alive = {"a": True, "b": True}
        first = BackgroundWorkerTicket(lambda: alive["a"])
        second = BackgroundWorkerTicket(lambda: alive["b"])
        self.gate.submit(first, lambda: self.started.append("a"))
        self.gate.submit(second, lambda: self.started.append("b"))
        third = BackgroundWorkerTicket(lambda: True)

        alive["a"] = False
        self.gate.submit(third, lambda: self.started.append("c"))

        self.assertEqual(self.started, ["a", "b", "c"])
        self.assertEqual(self.gate.active_count(), 2)

    def test_deleted_worker_does_not_hold_the_slot(self) -> None:
        def _deleted() -> bool:
            raise RuntimeError("wrapped C/C++ object has been deleted")

        first = BackgroundWorkerTicket(_deleted)
        self.gate.submit(first, lambda: self.started.append("a"))
        self.gate.submit(BackgroundWorkerTicket(lambda: True), lambda: self.started.append("b"))
        queued = BackgroundWorkerTicket(lambda: True)

        self.gate.submit(queued, lambda: self.started.append("c"))

        self.assertEqual(self.started, ["a", "b", "c"])
        self.assertFalse(queued.is_pending())

    def test_failed_start_frees_the_slot(self) -> None:
        def _boom() -> None:
            raise RuntimeError("start failed")

        ticket = BackgroundWorkerTicket()
        with self.assertRaises(RuntimeError):
            self.gate.submit(ticket, _boom)

        self.assertEqual(self.gate.active_count(), 0)


class OneShotWorkerRuntimeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = BackgroundWorkerGate(limit=1)
        reset_background_worker_gate(self.gate)
        self.addCleanup(reset_background_worker_gate, None)

    @staticmethod
    def _start(runtime: OneShotWorkerRuntime, worker: _FakeWorker) -> None:
        runtime.start_qthread_worker(worker_factory=lambda _request_id: worker)

    def test_queued_worker_counts_as_running(self) -> None:
        first_runtime = OneShotWorkerRuntime()
        second_runtime = OneShotWorkerRuntime()
        first = _FakeWorker()
        second = _FakeWorker()

        self._start(first_runtime, first)
        self._start(second_runtime, second)

        self.assertTrue(first.started)
        self.assertFalse(second.started)
        self.assertTrue(second_runtime.is_queued())
        self.assertTrue(second_runtime.is_running())

    def test_finished_worker_releases_slot_for_queued_worker(self) -> None:
        first_runtime = OneShotWorkerRuntime()
        second_runtime = OneShotWorkerRuntime()
        first = _FakeWorker()
        second = _FakeWorker()
        self._start(first_runtime, first)
        self._start(second_runtime, second)

        first.finish()

        self.assertTrue(second.started)
        self.assertFalse(second_runtime.is_queued())

    def test_restart_discards_worker_that_never_left_the_queue(self) -> None:
        blocking_runtime = OneShotWorkerRuntime()
        runtime = OneShotWorkerRuntime()
        blocking = _FakeWorker()
        stale = _FakeWorker()
        fresh = _FakeWorker()
        self._start(blocking_runtime, blocking)
        self._start(runtime, stale)

        self._start(runtime, fresh)

        self.assertTrue(stale.deleted)
        self.assertFalse(stale.started)
        self.assertEqual(self.gate.queued_count(), 1)
        self.assertIs(runtime.worker, fresh)

    def test_stop_removes_queued_worker_from_gate(self) -> None:
        blocking_runtime = OneShotWorkerRuntime()
        runtime = OneShotWorkerRuntime()
        self._start(blocking_runtime, _FakeWorker())
        queued = _FakeWorker()
        self._start(runtime, queued)

        runtime.stop()

        self.assertEqual(self.gate.queued_count(), 0)
        self.assertFalse(queued.started)
        self.assertFalse(runtime.is_running())

    def test_cancel_removes_queued_worker_from_gate(self) -> None:
        blocking_runtime = OneShotWorkerRuntime()
        runtime = OneShotWorkerRuntime()
        self._start(blocking_runtime, _FakeWorker())
        queued = _FakeWorker()
        self._start(runtime, queued)

        runtime.cancel()

        self.assertEqual(self.gate.queued_count(), 0)
        self.assertFalse(queued.started)

    def test_default_gate_is_shared_singleton(self) -> None:
        reset_background_worker_gate(None)
        self.assertIs(background_worker_gate(), background_worker_gate())


class BackgroundWorkerGateBoundaryTests(unittest.TestCase):
    def test_ui_worker_runtime_starts_through_the_gate(self) -> None:
        import inspect

        source = inspect.getsource(OneShotWorkerRuntime)

        self.assertIn("background_worker_gate().submit", source)

    def test_dpi_runtime_pipeline_bypasses_the_gate(self) -> None:
        import inspect

        from winws_runtime.runtime import thread_runtime

        source = inspect.getsource(thread_runtime)

        # Запуск/остановка/переключение DPI — критический путь: он не должен
        # ждать очереди фоновых загрузчиков страниц.
        self.assertNotIn("background_worker_gate", source)
        self.assertIn("thread.start()", source)


if __name__ == "__main__":
    unittest.main()
