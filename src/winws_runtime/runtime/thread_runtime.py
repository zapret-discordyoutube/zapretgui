from __future__ import annotations

from PyQt6.QtCore import QThread, Qt

from log.log import log
from ui.ui_thread_guard import build_background_worker_launcher



def start_worker_thread(
    owner,
    *,
    thread_attr: str,
    worker_attr: str,
    worker,
    finished_slot=None,
    progress_slot=None,
    cleanup_log_label: str = "worker thread",
) -> QThread:
    thread = QThread()
    setattr(owner, thread_attr, thread)
    setattr(owner, worker_attr, worker)

    worker.moveToThread(thread)
    # В Nuitka обычный AutoConnection уже возвращал тяжёлый run() в GUI-поток.
    # QThread.started испускается новым потоком, поэтому прямое соединение здесь
    # гарантирует выполнение worker-а вне интерфейса.
    thread.started.connect(
        build_background_worker_launcher(worker, worker.run, "run"),
        Qt.ConnectionType.DirectConnection,
    )

    progress_signal = getattr(worker, "progress", None)
    if progress_slot is not None and progress_signal is not None:
        progress_signal.connect(progress_slot)

    finished_signal = getattr(worker, "finished", None)
    if finished_signal is None:
        raise RuntimeError(f"{type(worker).__name__} does not expose finished signal")

    if finished_slot is not None:
        finished_signal.connect(finished_slot)

    def cleanup_worker(*_args):
        try:
            current_thread = getattr(owner, thread_attr, None)
            if current_thread:
                current_thread.quit()

            current_worker = getattr(owner, worker_attr, None)
            if current_worker is not None:
                current_worker.deleteLater()
                setattr(owner, worker_attr, None)
        except Exception as e:
            log(f"Ошибка при очистке {cleanup_log_label}: {e}", "❌ ERROR")

    def cleanup_thread(*_args):
        try:
            if getattr(owner, thread_attr, None) is thread:
                setattr(owner, thread_attr, None)
        except Exception as e:
            log(f"Ошибка при очистке {cleanup_log_label}: {e}", "❌ ERROR")

    finished_signal.connect(cleanup_worker)
    thread.finished.connect(cleanup_thread)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
