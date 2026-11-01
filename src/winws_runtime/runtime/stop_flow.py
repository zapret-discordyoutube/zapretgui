from __future__ import annotations

from PyQt6.QtCore import QTimer

from log.log import log
from winws_runtime.flow.start_preparation import resolve_method_name

from .control_workers import PresetLaunchStopWorker, StopAndExitWorker
from .status_feedback import runtime_owner_status_callback, set_runtime_owner_status
from .thread_runtime import start_worker_thread


# «Стоп» во время запуска ждёт его конца не дольше этого, потом останавливает как есть.
START_WAIT_FOR_STOP_MS = 30_000
START_WAIT_POLL_MS = 250


def _start_in_progress(runtime_owner) -> bool:
    try:
        thread = runtime_owner._dpi_start_thread
        return bool(thread is not None and thread.isRunning())
    except RuntimeError:
        runtime_owner._dpi_start_thread = None
        return False


def stop_dpi_async(
    runtime_owner,
    *,
    force_cleanup: bool = False,
    cleanup_services: bool = False,
    _waited_ms: int = 0,
) -> None:
    """Останавливает DPI через общий асинхронный pipeline."""
    try:
        if runtime_owner._dpi_stop_thread and runtime_owner._dpi_stop_thread.isRunning():
            log("Остановка DPI уже выполняется", "DEBUG")
            return
    except RuntimeError:
        runtime_owner._dpi_stop_thread = None

    if _start_in_progress(runtime_owner) and _waited_ms < START_WAIT_FOR_STOP_MS:
        # Запуск ещё готовится и winws не создал: остановка сейчас не нашла бы
        # процесса, сказала бы «уже остановлен», а запуск следом поднял бы
        # winws при нажатом «Стоп». Ждём конца запуска и останавливаем уже его.
        if _waited_ms == 0:
            log("Остановка DPI ждёт конца запуска", "DEBUG")
        QTimer.singleShot(
            START_WAIT_POLL_MS,
            lambda: stop_dpi_async(
                runtime_owner,
                force_cleanup=force_cleanup,
                cleanup_services=cleanup_services,
                _waited_ms=_waited_ms + START_WAIT_POLL_MS,
            ),
        )
        return

    snapshot = runtime_owner._runtime_service().snapshot()
    launch_method = str(getattr(snapshot, "launch_method", "") or "").strip().lower()
    method_name = resolve_method_name(launch_method)
    set_runtime_owner_status(runtime_owner, f"🛑 Остановка DPI ({method_name})...")

    runtime_owner._runtime_service().set_busy(True, "Остановка Zapret...")
    runtime_owner._begin_runtime_stop()

    start_worker_thread(
        runtime_owner,
        thread_attr="_dpi_stop_thread",
        worker_attr="_dpi_stop_worker",
        worker=PresetLaunchStopWorker(
            launch_method,
            runtime_feature=runtime_owner._runtime_feature,
            runtime_api=runtime_owner._runtime_api(),
            force_cleanup=force_cleanup,
            cleanup_services=cleanup_services,
        ),
        finished_slot=runtime_owner._on_dpi_stop_finished,
        progress_slot=runtime_owner_status_callback(runtime_owner),
        cleanup_log_label="потока остановки",
    )

    log(f"Запуск асинхронной остановки DPI (метод: {method_name})", "INFO")


def stop_and_exit_async(runtime_owner) -> None:
    """Останавливает DPI и закрывает программу через worker-поток."""
    runtime_owner._runtime_feature.lifecycle.mark_stop_and_exit_requested()

    start_worker_thread(
        runtime_owner,
        thread_attr="_stop_exit_thread",
        worker_attr="_stop_exit_worker",
        worker=StopAndExitWorker(
            runtime_feature=runtime_owner._runtime_feature,
        ),
        finished_slot=runtime_owner._on_stop_and_exit_finished,
        progress_slot=runtime_owner_status_callback(runtime_owner),
        cleanup_log_label="потока stop-and-exit",
    )
