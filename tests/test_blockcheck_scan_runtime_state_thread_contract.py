"""BlockCheck-сканер не обновляет runtime-state из своего фонового потока.

Pre/post-scan cleanup сканера выполняется в QThread. Если оттуда обновить
runtime-state, `MainWindowStateStore` синхронно позовёт UI-подписчиков (страницы
управления) в потоке сканера, тот начнёт править QWidget вне GUI-потока и окно
зависнет на «Запуск сканирования...». Поэтому процессную часть остановки делает
поток сканера, а runtime-state применяет GUI-поток.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


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
    from PyQt6.QtCore import QThread

    class _BodyThread(QThread):
        def run(self) -> None:
            body()

    thread = _BodyThread()
    thread.start()
    thread.wait(10000)
    return thread


class _FakeRuntimeApi:
    """Минимальный launch runtime API: остановка без реальных процессов."""

    def __init__(self, *, still_running: bool = False) -> None:
        self._still_running = bool(still_running)

    def has_residual_processes(self, silent: bool = False) -> bool:
        return self._still_running

    def stop_all_processes(self) -> bool:
        return True

    def cleanup_windivert_service(self) -> bool:
        return True


def _build_runtime_feature(*, still_running: bool = False):
    from PyQt6.QtWidgets import QApplication

    from app.feature_facades.runtime import RuntimeFeature
    from app.state_store import MainWindowStateStore
    from app.ui_thread_marshaller import QtUiThreadMarshaller
    from winws_runtime.state.launch_runtime_service import LaunchRuntimeService

    app = QApplication.instance() or QApplication([])
    store = MainWindowStateStore(
        LaunchRuntimeService.build_initial_ui_state(
            launch_method="zapret2",
            dpi_autostart_enabled=False,
            launch_supported=True,
        ),
        ui_thread_marshaller=QtUiThreadMarshaller(),
    )
    runtime_service = LaunchRuntimeService(store)
    runtime_service.bootstrap_probe(True, launch_method="zapret2")

    feature = RuntimeFeature(
        qt_parent=None,
        runtime_service=runtime_service,
        presets_feature=None,
        profile_feature=None,
        ui_state=store,
        orchestra_feature=None,
        startup_state=None,
        mark_stop_and_exit_requested=None,
    )
    feature.objects.launch_runtime_api = _FakeRuntimeApi(still_running=still_running)
    return app, store, feature, runtime_service


class BlockcheckScanShutdownWiringTests(unittest.TestCase):
    def test_page_kwargs_pass_worker_variant_of_shutdown_sync(self) -> None:
        from ui.page_deps.system import build_blockcheck_page_kwargs
        from ui.page_registry import PageName

        blockcheck_feature = SimpleNamespace(create_strategy_scan_worker=Mock(return_value="worker"))
        runtime_feature = SimpleNamespace(
            shutdown_sync=Mock(name="shutdown_sync"),
            shutdown_sync_from_worker=Mock(name="shutdown_sync_from_worker"),
        )

        kwargs = build_blockcheck_page_kwargs(
            page_name=PageName.BLOCKCHECK,
            blockcheck_feature=blockcheck_feature,
            diagnostics_feature=object(),
            dns_feature=object(),
            runtime_feature=runtime_feature,
        )

        result = kwargs["create_strategy_scan_worker"](target="discord.com")

        self.assertEqual(result, "worker")
        blockcheck_feature.create_strategy_scan_worker.assert_called_once_with(
            target="discord.com",
            shutdown_sync=runtime_feature.shutdown_sync_from_worker,
        )

    def test_updater_runtime_actions_use_worker_variant_of_shutdown_sync(self) -> None:
        from ui.page_deps.system import build_servers_page_kwargs
        from ui.page_registry import PageName

        runtime_feature = SimpleNamespace(
            is_any_running=Mock(),
            shutdown_sync=Mock(name="shutdown_sync"),
            shutdown_sync_from_worker=Mock(name="shutdown_sync_from_worker"),
            is_available=Mock(),
            restart=Mock(),
            objects=SimpleNamespace(runtime_service=None),
        )

        kwargs = build_servers_page_kwargs(
            page_name=PageName.SERVERS,
            runtime_feature=runtime_feature,
            updater_feature=object(),
            external_actions_feature=SimpleNamespace(create_open_url_worker=Mock()),
            show_page=Mock(),
            request_exit=Mock(),
        )

        self.assertIs(
            kwargs["runtime_actions"].shutdown_sync,
            runtime_feature.shutdown_sync_from_worker,
        )


class ApplyRuntimeStateAfterShutdownTests(unittest.TestCase):
    def test_marks_stopped_when_nothing_left_running(self) -> None:
        from winws_runtime.runtime.sync_shutdown import apply_runtime_state_after_shutdown

        runtime_service = Mock()

        apply_runtime_state_after_shutdown(
            runtime_service=runtime_service,
            still_running=False,
            launch_method="zapret2",
        )

        runtime_service.mark_stopped.assert_called_once_with(clear_error=True)
        runtime_service.bootstrap_probe.assert_not_called()

    def test_bootstrap_probes_when_processes_survived(self) -> None:
        from winws_runtime.runtime.sync_shutdown import apply_runtime_state_after_shutdown

        runtime_service = Mock()

        apply_runtime_state_after_shutdown(
            runtime_service=runtime_service,
            still_running=True,
            launch_method="zapret2",
        )

        runtime_service.bootstrap_probe.assert_called_once_with(True, launch_method="zapret2")
        runtime_service.mark_stopped.assert_not_called()


class ShutdownSyncFromWorkerThreadContractTests(unittest.TestCase):
    def test_runtime_state_is_applied_in_gui_thread(self) -> None:
        app, store, feature, _runtime_service = _build_runtime_feature()
        gui_thread_id = threading.get_ident()
        calls: list[tuple[int, bool]] = []

        store.subscribe(
            lambda state, _changed: calls.append((threading.get_ident(), bool(state.launch_running))),
            fields={"launch_running", "launch_phase"},
        )

        _run_in_qthread(lambda: feature.shutdown_sync_from_worker(reason="blockcheck_pre_scan"))

        # Пока GUI-поток не прокрутил событий, подписчики не тронуты.
        self.assertEqual(calls, [])
        self.assertTrue(_process_events_until(app, lambda: bool(calls)))
        self.assertEqual(calls[0][0], gui_thread_id)
        self.assertIs(calls[0][1], False)
        self.assertFalse(store.snapshot().launch_running)

    def test_runtime_service_write_itself_happens_in_gui_thread(self) -> None:
        # Отдельно от доставки подписчиков: сама запись runtime-state не должна
        # выполняться в потоке воркера, иначе защита остаётся только на уровне store.
        app, _store, feature, runtime_service = _build_runtime_feature()
        gui_thread_id = threading.get_ident()
        write_threads: list[int] = []
        original_mark_stopped = runtime_service.mark_stopped

        def _mark_stopped(*args, **kwargs):
            write_threads.append(threading.get_ident())
            return original_mark_stopped(*args, **kwargs)

        runtime_service.mark_stopped = _mark_stopped

        _run_in_qthread(lambda: feature.shutdown_sync_from_worker(reason="blockcheck_pre_scan"))

        self.assertEqual(write_threads, [])
        self.assertTrue(_process_events_until(app, lambda: bool(write_threads)))
        self.assertEqual(write_threads, [gui_thread_id])

    def test_process_shutdown_runs_in_calling_thread(self) -> None:
        _app, _store, feature, _runtime_service = _build_runtime_feature()
        observed: dict[str, object] = {}
        api = feature.objects.launch_runtime_api
        original_stop = api.stop_all_processes

        def _stop_all_processes():
            observed["stop_thread_id"] = threading.get_ident()
            return original_stop()

        api.stop_all_processes = _stop_all_processes
        worker_thread_ids: list[int] = []

        def _body() -> None:
            worker_thread_ids.append(threading.get_ident())
            observed["result"] = feature.shutdown_sync_from_worker(reason="blockcheck_pre_scan")

        _run_in_qthread(_body)

        self.assertEqual(observed["stop_thread_id"], worker_thread_ids[0])
        self.assertNotEqual(worker_thread_ids[0], threading.get_ident())
        self.assertFalse(getattr(observed["result"], "still_running", True))

    def test_update_runtime_state_false_keeps_state_untouched(self) -> None:
        app, store, feature, _runtime_service = _build_runtime_feature()
        calls: list[frozenset] = []
        store.subscribe(lambda _state, changed: calls.append(changed), fields={"launch_running", "launch_phase"})

        _run_in_qthread(
            lambda: feature.shutdown_sync_from_worker(
                reason="updater_pipeline",
                update_runtime_state=False,
            )
        )
        _process_events_until(app, lambda: bool(calls), timeout=0.5)

        self.assertEqual(calls, [])
        self.assertTrue(store.snapshot().launch_running)


if __name__ == "__main__":
    unittest.main()
