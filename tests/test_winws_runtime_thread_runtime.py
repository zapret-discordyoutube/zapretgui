from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback, *_args) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in list(self._callbacks):
            callback(*args)


class _FakeThread:
    instances = []

    def __init__(self, *, running: bool = False) -> None:
        self.started = _Signal()
        self.finished = _Signal()
        self.running = bool(running)
        self.quit_called = False
        self.wait_called = False
        self.terminate_called = False
        self.delete_later_called = False
        _FakeThread.instances.append(self)

    def start(self) -> None:
        pass

    def quit(self) -> None:
        self.quit_called = True

    def isRunning(self) -> bool:  # noqa: N802
        return self.running

    def wait(self, *_args) -> bool:
        self.wait_called = True
        return True

    def terminate(self) -> None:
        self.terminate_called = True

    def deleteLater(self) -> None:
        self.delete_later_called = True


class _FakeWorker:
    def __init__(self) -> None:
        self.finished = _Signal()
        self.moved_to_thread = None
        self.delete_later_called = False

    def moveToThread(self, thread) -> None:
        self.moved_to_thread = thread

    def run(self) -> None:
        pass

    def deleteLater(self) -> None:
        self.delete_later_called = True


class WinwsRuntimeThreadRuntimeTests(unittest.TestCase):
    def test_worker_thread_cleanup_keeps_thread_until_qthread_finishes(self) -> None:
        from winws_runtime.runtime.thread_runtime import start_worker_thread

        owner = SimpleNamespace()
        worker = _FakeWorker()
        _FakeThread.instances = []

        with patch("winws_runtime.runtime.thread_runtime.QThread", _FakeThread):
            thread = start_worker_thread(
                owner,
                thread_attr="_thread",
                worker_attr="_worker",
                worker=worker,
            )

        worker.finished.emit(True, "")

        self.assertTrue(thread.quit_called)
        self.assertFalse(thread.wait_called)
        self.assertIsNone(owner._worker)
        self.assertIs(owner._thread, thread)

        thread.finished.emit()

        self.assertIsNone(owner._thread)

    def test_launch_runtime_cleanup_threads_keeps_running_threads_owned(self) -> None:
        from winws_runtime.runtime.lifecycle_feedback import cleanup_threads

        start_thread = _FakeThread(running=True)
        stop_thread = _FakeThread(running=True)
        owner = SimpleNamespace(
            _dpi_start_thread=start_thread,
            _dpi_stop_thread=stop_thread,
        )

        cleanup_threads(owner)

        self.assertTrue(start_thread.quit_called)
        self.assertTrue(stop_thread.quit_called)
        self.assertFalse(start_thread.wait_called)
        self.assertFalse(stop_thread.wait_called)
        self.assertFalse(start_thread.terminate_called)
        self.assertFalse(stop_thread.terminate_called)
        self.assertIs(owner._dpi_start_thread, start_thread)
        self.assertIs(owner._dpi_stop_thread, stop_thread)

    def test_launch_runtime_cleanup_threads_quits_running_stop_exit_thread(self) -> None:
        from winws_runtime.runtime.lifecycle_feedback import cleanup_threads

        stop_exit_thread = _FakeThread(running=True)
        owner = SimpleNamespace(
            _dpi_start_thread=None,
            _dpi_stop_thread=None,
            _stop_exit_thread=stop_exit_thread,
        )

        cleanup_threads(owner)

        self.assertTrue(stop_exit_thread.quit_called)
        self.assertFalse(stop_exit_thread.wait_called)
        self.assertFalse(stop_exit_thread.terminate_called)
        self.assertIs(owner._stop_exit_thread, stop_exit_thread)


if __name__ == "__main__":
    unittest.main()
