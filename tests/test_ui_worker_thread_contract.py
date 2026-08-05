"""Потоковый контракт фоновых воркеров интерфейса.

BlockCheck в собранном приложении исполнялся в GUI-потоке: `moveToThread` +
`thread.started.connect(worker.run)` доставляли работу обратно в поток
интерфейса, и окно висело до конца проверки. Здесь фиксируется, что запуск
воркера всегда происходит в его собственном потоке, а нарушение контракта
громко попадает в лог.
"""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace


def _process_events_until(app, predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


class OneShotWorkerThreadContractTests(unittest.TestCase):
    def test_qobject_worker_runs_outside_gui_thread(self) -> None:
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtWidgets import QApplication

        from ui.background_worker_gate import BackgroundWorkerGate, reset_background_worker_gate
        from ui.one_shot_worker_runtime import OneShotWorkerRuntime

        app = QApplication.instance() or QApplication([])
        reset_background_worker_gate(BackgroundWorkerGate(limit=4))
        self.addCleanup(reset_background_worker_gate, None)

        gui_thread_id = threading.get_ident()
        observed: dict[str, object] = {}

        class _Worker(QObject):
            finished = pyqtSignal(object)

            def run(self):
                observed["run_thread_id"] = threading.get_ident()
                self.finished.emit(None)

        runtime = OneShotWorkerRuntime()
        keep_alive = []

        def _factory(_request_id):
            worker = _Worker()
            keep_alive.append(worker)
            return worker

        _request_id, worker, thread = runtime.start_qobject_worker(
            parent=None,
            worker_factory=_factory,
        )
        keep_alive.append(thread)

        self.assertTrue(_process_events_until(app, lambda: "run_thread_id" in observed))
        self.assertNotEqual(observed["run_thread_id"], gui_thread_id)
        _process_events_until(app, lambda: not thread.isRunning(), timeout=5.0)

    def test_worker_signals_still_arrive_in_gui_thread(self) -> None:
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtWidgets import QApplication

        from ui.background_worker_gate import BackgroundWorkerGate, reset_background_worker_gate
        from ui.one_shot_worker_runtime import OneShotWorkerRuntime

        app = QApplication.instance() or QApplication([])
        reset_background_worker_gate(BackgroundWorkerGate(limit=4))
        self.addCleanup(reset_background_worker_gate, None)

        gui_thread_id = threading.get_ident()
        observed: dict[str, object] = {}

        class _Worker(QObject):
            finished = pyqtSignal(object)
            progress = pyqtSignal(str)

            def run(self):
                self.progress.emit("payload")
                self.finished.emit(None)

        runtime = OneShotWorkerRuntime()
        keep_alive = []

        def _factory(_request_id):
            worker = _Worker()
            worker.progress.connect(
                lambda _text: observed.setdefault("signal_thread_id", threading.get_ident())
            )
            keep_alive.append(worker)
            return worker

        _request_id, _worker, thread = runtime.start_qobject_worker(
            parent=None,
            worker_factory=_factory,
        )
        keep_alive.append(thread)

        self.assertTrue(_process_events_until(app, lambda: "signal_thread_id" in observed))
        self.assertEqual(observed["signal_thread_id"], gui_thread_id)
        _process_events_until(app, lambda: not thread.isRunning(), timeout=5.0)


class RuntimeWorkerThreadContractTests(unittest.TestCase):
    def test_runtime_worker_runs_outside_gui_thread(self) -> None:
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtWidgets import QApplication

        from winws_runtime.runtime.thread_runtime import start_worker_thread

        app = QApplication.instance() or QApplication([])
        gui_thread_id = threading.get_ident()
        observed: dict[str, object] = {}

        class _Worker(QObject):
            progress = pyqtSignal(str)
            finished = pyqtSignal(bool, str)

            def run(self) -> None:
                observed["run_thread_id"] = threading.get_ident()
                self.progress.emit("Применяем пресет...")
                self.finished.emit(True, "")

        owner = SimpleNamespace()
        thread = start_worker_thread(
            owner,
            thread_attr="_thread",
            worker_attr="_worker",
            worker=_Worker(),
            progress_slot=lambda _text: observed.setdefault(
                "progress_thread_id",
                threading.get_ident(),
            ),
            finished_slot=lambda *_args: observed.setdefault(
                "finished_thread_id",
                threading.get_ident(),
            ),
        )

        expected_events = {
            "run_thread_id",
            "progress_thread_id",
            "finished_thread_id",
        }
        self.assertTrue(_process_events_until(app, lambda: expected_events <= set(observed)))
        self.assertNotEqual(observed["run_thread_id"], gui_thread_id)
        self.assertEqual(observed["progress_thread_id"], gui_thread_id)
        self.assertEqual(observed["finished_thread_id"], gui_thread_id)
        _process_events_until(app, lambda: not thread.isRunning(), timeout=5.0)


class WorkerStartWiringTests(unittest.TestCase):
    def test_started_signal_uses_direct_connection(self) -> None:
        # В обычном Python работа уходит в свой поток при любом типе
        # соединения, поэтому поведенческий тест эту регрессию не ловит:
        # в собранном приложении (Nuitka) без DirectConnection `run`
        # доставлялся обратно в GUI-поток. Фиксируем сам способ подключения.
        import inspect

        from ui.one_shot_worker_runtime import OneShotWorkerRuntime

        source = inspect.getsource(OneShotWorkerRuntime.start_qobject_worker)

        self.assertIn("DirectConnection", source)
        self.assertIn("build_background_worker_launcher", source)

    def test_runtime_started_signal_uses_direct_connection(self) -> None:
        import inspect

        from winws_runtime.runtime.thread_runtime import start_worker_thread

        source = inspect.getsource(start_worker_thread)

        self.assertIn("DirectConnection", source)
        self.assertIn("build_background_worker_launcher", source)

    def test_launcher_checks_thread_contract(self) -> None:
        import inspect

        from ui.ui_thread_guard import build_background_worker_launcher

        source = inspect.getsource(build_background_worker_launcher)

        self.assertIn("ensure_background_thread", source)


class UiThreadGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        from ui.ui_thread_guard import reset_gui_thread_marker

        reset_gui_thread_marker()
        self.addCleanup(reset_gui_thread_marker)

    def test_reports_violation_once_per_context(self) -> None:
        from ui.ui_thread_guard import ensure_background_thread, mark_gui_thread

        mark_gui_thread()
        records: list[tuple[str, str]] = []

        import log.log as log_module

        original_log = log_module.log
        log_module.log = lambda message, level="INFO", component=None: records.append((message, level))
        self.addCleanup(lambda: setattr(log_module, "log", original_log))

        self.assertFalse(ensure_background_thread("Worker.run"))
        self.assertFalse(ensure_background_thread("Worker.run"))

        self.assertEqual(len(records), 1)
        self.assertIn("Worker.run", records[0][0])
        self.assertIn("ERROR", records[0][1])

    def test_background_thread_passes(self) -> None:
        from ui.ui_thread_guard import ensure_background_thread, mark_gui_thread

        mark_gui_thread()
        result: list[bool] = []

        thread = threading.Thread(target=lambda: result.append(ensure_background_thread("Worker.run")))
        thread.start()
        thread.join(5)

        self.assertEqual(result, [True])


if __name__ == "__main__":
    unittest.main()
