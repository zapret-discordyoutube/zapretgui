from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QThread, Qt

from ui.background_worker_gate import BackgroundWorkerTicket, background_worker_gate
from ui.ui_thread_guard import ensure_background_thread


def _worker_label(worker, run_method_name: str) -> str:
    try:
        return f"{type(worker).__name__}.{run_method_name}"
    except Exception:
        return str(run_method_name or "worker.run")


def _build_worker_launcher(worker, run_method: Callable[[], object], run_method_name: str):
    """Точка входа воркера с проверкой потока.

    Замыкание вызывается по DirectConnection из `QThread.started`, то есть уже
    внутри рабочего потока. Проверка нужна как страховка: если работа всё же
    оказалась в GUI-потоке, это видно в логе, а не только по зависшему окну.
    """
    label = _worker_label(worker, run_method_name)

    def _launch() -> None:
        ensure_background_thread(label)
        run_method()

    return _launch


def _verify_worker_affinity(worker, thread: QThread) -> None:
    """Логирует случай, когда worker не переехал в свой поток."""
    try:
        if worker.thread() is thread:
            return
    except (AttributeError, RuntimeError):
        return
    try:
        from log.log import log

        log(
            f"Worker {type(worker).__name__} остался в потоке "
            f"{worker.thread()} вместо {thread} — его сигналы пойдут не из рабочего потока",
            "⚠ WARNING",
        )
    except Exception:
        pass


def _worker_running_probe(target) -> Callable[[], bool]:
    """Отвечает гейту, считает ли ещё этот воркер.

    Гейт не должен зависеть только от finished-сигнала: тесты и cleanup-пути
    удаляют воркеров, не доводя их до завершения.
    """

    def _probe() -> bool:
        is_running = getattr(target, "isRunning", None)
        if not callable(is_running):
            return True
        return bool(is_running())

    return _probe


class OneShotWorkerRuntime:
    """Общий запуск одноразового фонового worker-а.

    Worker здесь — фоновый загрузчик. Он делает тяжёлую работу вне UI-потока,
    а страница принимает только свежий результат по request_id.

    Старт проходит через общий гейт: параллельно работает ограниченное число
    фоновых воркеров, остальные ждут очереди, чтобы не отнимать GIL у
    GUI-потока (см. ui/background_worker_gate.py).
    """

    def __init__(self) -> None:
        self.request_id = 0
        self.worker = None
        self.thread = None
        self._ticket: BackgroundWorkerTicket | None = None

    def next_request_id(self) -> int:
        self.request_id += 1
        return self.request_id

    def is_current(self, request_id: int, *, cleanup_in_progress: bool = False) -> bool:
        return (not cleanup_in_progress) and int(request_id) == int(self.request_id)

    def _current_ticket(self) -> BackgroundWorkerTicket | None:
        # Тесты создают runtime через __new__ и обходят __init__: атрибута
        # может не быть.
        return self.__dict__.get("_ticket")

    def is_queued(self) -> bool:
        """True, пока воркер ждёт очереди гейта и ещё не стартовал."""
        ticket = self._current_ticket()
        return bool(ticket is not None and ticket.is_pending())

    def is_running(self) -> bool:
        if self.is_queued():
            return True
        target = self.thread or self.worker
        if target is None:
            return False
        try:
            if hasattr(target, "is_running"):
                running_state = getattr(target, "is_running")
                return bool(running_state() if callable(running_state) else running_state)
            return bool(target.isRunning())
        except (AttributeError, RuntimeError):
            self.worker = None
            self.thread = None
            return False

    def start_qobject_worker(
        self,
        *,
        parent,
        worker_factory: Callable[[int], object],
        on_loaded: Callable | None = None,
        on_failed: Callable | None = None,
        on_finished: Callable | None = None,
        bind_worker: Callable[[object], None] | None = None,
        run_method_name: str = "run",
        failed_signal_name: str = "failed",
    ) -> tuple[int, object, QThread]:
        request_id = self.next_request_id()
        thread = QThread(parent)
        worker = worker_factory(request_id)
        worker.moveToThread(thread)
        _verify_worker_affinity(worker, thread)

        run_method = getattr(worker, run_method_name)
        # DirectConnection обязателен: `started` эмитится уже внутри нового
        # потока, поэтому прямой вызов гарантированно исполняет работу там.
        # Без него доставка зависит от того, распознал ли PyQt получателя как
        # QObject; в собранном приложении этот путь уводил работу обратно в
        # GUI-поток, и окно висело до конца проверки.
        thread.started.connect(
            _build_worker_launcher(worker, run_method, run_method_name),
            Qt.ConnectionType.DirectConnection,
        )
        if on_loaded is not None and hasattr(worker, "loaded"):
            worker.loaded.connect(lambda *args, req=request_id: on_loaded(req, *args))
        failed_signal = getattr(worker, failed_signal_name, None)
        if on_failed is not None and failed_signal is not None:
            failed_signal.connect(lambda *args, req=request_id: on_failed(req, *args))
        worker.finished.connect(thread.quit)
        if failed_signal is not None:
            failed_signal.connect(thread.quit)
            failed_signal.connect(worker.deleteLater)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda req=request_id, th=thread: self._finish_qobject_worker(req, th, on_finished))
        thread.finished.connect(thread.deleteLater)
        if bind_worker is not None:
            bind_worker(worker)

        self._discard_queued_start()
        self.worker = worker
        self.thread = thread
        ticket = BackgroundWorkerTicket(_worker_running_probe(thread))
        self._ticket = ticket
        thread.finished.connect(lambda *_args, t=ticket: self._release_ticket(t))
        background_worker_gate().submit(ticket, lambda th=thread, t=ticket: self._start_thread(th, t))
        return request_id, worker, thread

    def start_qthread_worker(
        self,
        *,
        worker_factory: Callable[[int], object],
        on_loaded: Callable | None = None,
        on_failed: Callable | None = None,
        on_finished: Callable | None = None,
        bind_worker: Callable[[object], None] | None = None,
        signal_includes_request_id: bool = True,
        loaded_signal_name: str = "loaded",
        failed_signal_name: str = "failed",
    ) -> tuple[int, object]:
        request_id = self.next_request_id()
        worker = worker_factory(request_id)

        loaded_signal = getattr(worker, loaded_signal_name, None)
        if on_loaded is not None and loaded_signal is not None:
            if signal_includes_request_id:
                loaded_signal.connect(on_loaded)
            else:
                loaded_signal.connect(lambda *args, req=request_id: on_loaded(req, *args))
        failed_signal = getattr(worker, failed_signal_name, None)
        if on_failed is not None and failed_signal is not None:
            if signal_includes_request_id:
                failed_signal.connect(on_failed)
            else:
                failed_signal.connect(lambda *args, req=request_id: on_failed(req, *args))
        worker.finished.connect(lambda *_args, w=worker: self._finish_qthread_worker(w, on_finished))
        worker.finished.connect(worker.deleteLater)
        if bind_worker is not None:
            bind_worker(worker)

        self._discard_queued_start()
        self.worker = worker
        self.thread = None
        ticket = BackgroundWorkerTicket(_worker_running_probe(worker))
        self._ticket = ticket
        worker.finished.connect(lambda *_args, t=ticket: self._release_ticket(t))
        background_worker_gate().submit(ticket, lambda w=worker, t=ticket: self._start_thread(w, t))
        return request_id, worker

    def _start_thread(self, target, ticket: BackgroundWorkerTicket) -> None:
        try:
            target.start()
        except RuntimeError:
            # Пока воркер ждал очереди, его C++-объект успели удалить вместе
            # со страницей: слот держать не за что.
            self._release_ticket(ticket)

    def _release_ticket(self, ticket: BackgroundWorkerTicket) -> None:
        background_worker_gate().release(ticket)
        if self._current_ticket() is ticket:
            self._ticket = None

    def _discard_queued_start(self) -> None:
        """Снимает предыдущий воркер, который так и не дождался очереди.

        Его результат всё равно был бы отброшен по request_id, а сам QThread
        без старта не эмитит finished и не удалился бы сам.
        """
        ticket = self._current_ticket()
        if ticket is None:
            return
        if not ticket.is_pending():
            # Воркер уже работает: слот отпустит его собственный finished.
            self._ticket = None
            return
        background_worker_gate().cancel(ticket)
        self._ticket = None
        for target in (self.worker, self.thread):
            if target is None:
                continue
            delete_later = getattr(target, "deleteLater", None)
            if callable(delete_later):
                try:
                    delete_later()
                except RuntimeError:
                    pass
        self.worker = None
        self.thread = None

    def stop(
        self,
        *,
        blocking: bool = False,
        wait_timeout_ms: int = 2000,
        terminate_wait_ms: int = 500,
        log_fn: Callable[[str, str], None] | None = None,
        warning_prefix: str = "Worker",
    ) -> None:
        if self.is_queued():
            # Воркер ещё не стартовал: снимаем его из очереди, иначе гейт
            # запустит уже никому не нужную работу.
            self._discard_queued_start()
            return

        worker = self.worker
        thread = self.thread

        if worker is not None:
            stop = getattr(worker, "stop", None)
            if callable(stop):
                try:
                    stop()
                except RuntimeError:
                    worker = None
                    self.worker = None
                except Exception as exc:
                    if log_fn is not None:
                        log_fn(f"Ошибка остановки {warning_prefix}: {exc}", "DEBUG")

        target = thread or worker
        if target is None:
            return
        try:
            if hasattr(target, "is_running"):
                running_state = getattr(target, "is_running")
                running = bool(running_state() if callable(running_state) else running_state)
            else:
                running = bool(target.isRunning())
        except (AttributeError, RuntimeError):
            self.worker = None
            self.thread = None
            return
        if not running:
            if self.worker is worker:
                self.worker = None
            if self.thread is thread:
                self.thread = None
            return

        quit_fn = getattr(target, "quit", None)
        if callable(quit_fn):
            quit_fn()
        if blocking and hasattr(target, "wait") and not target.wait(wait_timeout_ms):
            if log_fn is not None:
                log_fn(f"⚠ {warning_prefix} не завершился, принудительно завершаем", "WARNING")
            terminate = getattr(target, "terminate", None)
            if callable(terminate):
                try:
                    terminate()
                    target.wait(terminate_wait_ms)
                except Exception:
                    pass

    def cancel(self) -> None:
        self.request_id += 1
        self._discard_queued_start()
        self.worker = None
        self.thread = None

    def _finish_qobject_worker(self, request_id: int, thread, on_finished: Callable | None) -> None:
        if self.thread is not thread:
            return
        self.thread = None
        self.worker = None
        if on_finished is not None:
            on_finished(request_id, thread)

    def _finish_qthread_worker(self, worker, on_finished: Callable | None) -> None:
        if self.worker is not worker:
            return
        self.worker = None
        if on_finished is not None:
            on_finished(worker)


__all__ = ["OneShotWorkerRuntime"]
