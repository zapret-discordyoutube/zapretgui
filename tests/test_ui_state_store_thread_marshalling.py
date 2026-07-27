"""Потоковый контракт MainWindowStateStore.

Подписчики store — живые виджеты. Доставка колбэков из фонового потока правит
QWidget вне GUI-потока: на Windows такой вызов уходит в SendMessage к оконному
потоку, который не может ответить, пока фоновый поток держит GIL, — окно
зависает навсегда (баг «BlockCheck: программа зависает при старте сканирования»).
"""

from __future__ import annotations

import threading
import time
import unittest


def _process_events_until(app, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def _run_in_qthread(body):
    """Запускает `body` в отдельном QThread и ждёт его завершения."""
    from PyQt6.QtCore import QThread

    class _BodyThread(QThread):
        def run(self) -> None:
            body()

    thread = _BodyThread()
    thread.start()
    thread.wait(10000)
    return thread


class QtUiThreadMarshallerTests(unittest.TestCase):
    def test_reports_background_thread_and_runs_action_in_gui_thread(self) -> None:
        from PyQt6.QtWidgets import QApplication

        from app.ui_thread_marshaller import QtUiThreadMarshaller

        app = QApplication.instance() or QApplication([])
        marshaller = QtUiThreadMarshaller()
        gui_thread_id = threading.get_ident()
        observed: dict[str, object] = {}

        def _body() -> None:
            observed["is_ui_thread_in_worker"] = marshaller.is_ui_thread()
            marshaller.post(lambda: observed.__setitem__("action_thread_id", threading.get_ident()))

        _run_in_qthread(_body)

        self.assertIs(observed["is_ui_thread_in_worker"], False)
        self.assertNotIn("action_thread_id", observed)  # доставка отложена до event loop
        self.assertTrue(_process_events_until(app, lambda: "action_thread_id" in observed))
        self.assertEqual(observed["action_thread_id"], gui_thread_id)
        self.assertTrue(marshaller.is_ui_thread())


class MainWindowStateStoreThreadMarshallingTests(unittest.TestCase):
    def _build_store(self):
        from PyQt6.QtWidgets import QApplication

        from app.state_store import AppUiState, MainWindowStateStore
        from app.ui_thread_marshaller import QtUiThreadMarshaller

        app = QApplication.instance() or QApplication([])
        store = MainWindowStateStore(
            AppUiState(launch_running=True, launch_phase="running"),
            ui_thread_marshaller=QtUiThreadMarshaller(),
        )
        return app, store

    def test_background_update_delivers_callbacks_in_gui_thread(self) -> None:
        app, store = self._build_store()
        gui_thread_id = threading.get_ident()
        calls: list[tuple[int, bool, frozenset]] = []

        store.subscribe(
            lambda state, changed: calls.append((threading.get_ident(), state.launch_running, changed)),
            fields={"launch_running", "launch_phase"},
        )

        _run_in_qthread(lambda: store.update(launch_running=False, launch_phase="stopped"))

        # Состояние применяется сразу, а доставка — только в GUI-потоке.
        self.assertFalse(store.snapshot().launch_running)
        self.assertEqual(calls, [])
        self.assertTrue(_process_events_until(app, lambda: bool(calls)))
        self.assertEqual(len(calls), 1)
        thread_id, running, changed = calls[0]
        self.assertEqual(thread_id, gui_thread_id)
        self.assertIs(running, False)
        self.assertEqual(changed, frozenset({"launch_running", "launch_phase"}))

    def test_gui_thread_update_stays_synchronous(self) -> None:
        _app, store = self._build_store()
        calls: list[frozenset] = []

        store.subscribe(lambda _state, changed: calls.append(changed), fields={"launch_running"})

        self.assertTrue(store.update(launch_running=False))
        self.assertEqual(calls, [frozenset({"launch_running"})])

    def test_unsubscribed_callback_is_not_called_after_deferred_delivery(self) -> None:
        app, store = self._build_store()
        stale_calls: list[frozenset] = []
        live_calls: list[frozenset] = []

        unsubscribe = store.subscribe(
            lambda _state, changed: stale_calls.append(changed),
            fields={"launch_running"},
        )
        store.subscribe(lambda _state, changed: live_calls.append(changed), fields={"launch_running"})

        _run_in_qthread(lambda: store.update(launch_running=False))
        unsubscribe()

        self.assertTrue(_process_events_until(app, lambda: bool(live_calls)))
        self.assertEqual(stale_calls, [])
        self.assertEqual(live_calls, [frozenset({"launch_running"})])

    def test_post_to_ui_thread_runs_immediately_inside_gui_thread(self) -> None:
        _app, store = self._build_store()
        calls: list[int] = []

        store.post_to_ui_thread(lambda: calls.append(threading.get_ident()))

        self.assertEqual(calls, [threading.get_ident()])

    def test_store_without_marshaller_keeps_synchronous_delivery(self) -> None:
        from app.state_store import AppUiState, MainWindowStateStore

        store = MainWindowStateStore(AppUiState(launch_running=True))
        calls: list[int] = []
        store.subscribe(lambda _state, _changed: calls.append(threading.get_ident()), fields={"launch_running"})

        worker_thread_ids: list[int] = []

        def _body() -> None:
            worker_thread_ids.append(threading.get_ident())
            store.update(launch_running=False)

        thread = threading.Thread(target=_body)
        thread.start()
        thread.join(10)

        self.assertEqual(calls, worker_thread_ids)


if __name__ == "__main__":
    unittest.main()
