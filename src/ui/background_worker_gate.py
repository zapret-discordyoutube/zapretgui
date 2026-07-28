"""Ограничитель параллелизма фоновых UI-воркеров.

Одно действие пользователя поднимает сразу несколько фоновых загрузчиков:
переключение пресета, например, запускает пересчёт списка профилей, summary
control page, дополнительные настройки, резолв watcher-пути и перезагрузку
страниц. Каждый из них — CPU-bound Python, поэтому одновременная работа не
ускоряет результат (замеры: при строгой сериализации суммарное время даже
меньше), зато отнимает GIL у GUI-потока и кадры начинают идти рывками.

Гейт пропускает не более :data:`BACKGROUND_WORKER_LIMIT` воркеров одновременно,
остальные ждут в FIFO-очереди. Критический runtime-путь (запуск/остановка DPI,
переключение пресета) через гейт не проходит — он живёт в
``winws_runtime/runtime/thread_runtime.py`` и стартует немедленно.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


BACKGROUND_WORKER_LIMIT = 2

# Долгий воркер (сетевой запрос, скан, чтение большого файла) не должен держать
# очередь коротких UI-задач: через это время слот отпускается, а сам воркер
# продолжает работать. Это же значение — верхняя граница ожидания в очереди:
# дольше него старт не задержится, даже если оба слота заняты долгими задачами.
LONG_RUNNING_SLOT_RELEASE_MS = 800


class BackgroundWorkerTicket:
    """Место воркера в гейте: сначала в очереди, потом в активном слоте."""

    __slots__ = ("_state", "_started_at", "_is_worker_running")

    def __init__(self, is_worker_running: Callable[[], bool] | None = None) -> None:
        self._state = "new"
        self._started_at = 0.0
        self._is_worker_running = is_worker_running

    @property
    def state(self) -> str:
        return self._state

    def is_pending(self) -> bool:
        """True, пока воркер ещё ждёт своей очереди."""
        return self._state == "queued"

    def is_active(self) -> bool:
        return self._state == "active"

    def is_finished(self) -> bool:
        return self._state in {"released", "cancelled"}

    def slot_is_stale(self, *, now: float, max_age_sec: float) -> bool:
        """True, если активный слот пора отобрать.

        Держать слот имеет смысл, только пока воркер действительно считает.
        Если его finished потерялся (страницу снесли, поток так и не
        стартовал) или работа затянулась, очередь не должна стоять из-за него.
        """
        if self._state != "active":
            return False
        is_worker_running = self._is_worker_running
        if is_worker_running is not None:
            try:
                if not bool(is_worker_running()):
                    return True
            except RuntimeError:
                return True
        return bool(max_age_sec > 0 and (now - self._started_at) >= max_age_sec)


class BackgroundWorkerGate(QObject):
    """Пропускает ограниченное число фоновых воркеров одновременно."""

    _pump_requested = pyqtSignal()

    def __init__(self, *, limit: int = BACKGROUND_WORKER_LIMIT, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._limit = max(1, int(limit))
        self._lock = threading.RLock()
        self._active: set[BackgroundWorkerTicket] = set()
        self._queue: deque[tuple[BackgroundWorkerTicket, Callable[[], None]]] = deque()
        # release() приходит из потока воркера — старт следующего обязан
        # выполниться в GUI-потоке, где живёт сам гейт.
        self._pump_requested.connect(self._pump)

    @property
    def limit(self) -> int:
        return self._limit

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def queued_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def submit(self, ticket: BackgroundWorkerTicket, start: Callable[[], None]) -> None:
        """Запускает ``start`` сразу или ставит в очередь, если нет слота."""
        with self._lock:
            if ticket.is_finished():
                return
            self._reclaim_stale_slots_locked()
            if len(self._active) < self._limit:
                self._occupy_locked(ticket)
                run_now = True
            else:
                ticket._state = "queued"
                self._queue.append((ticket, start))
                run_now = False
        if run_now:
            self._run_start(ticket, start)

    def release(self, ticket: BackgroundWorkerTicket | None) -> None:
        """Освобождает слот (или убирает воркер из очереди)."""
        if ticket is None:
            return
        with self._lock:
            was_active = ticket in self._active
            self._active.discard(ticket)
            if not was_active:
                self._drop_from_queue_locked(ticket)
            ticket._state = "released"
        self._request_pump()

    def cancel(self, ticket: BackgroundWorkerTicket | None) -> bool:
        """Снимает воркер, который ещё не стартовал. Возвращает True, если успели."""
        if ticket is None:
            return False
        with self._lock:
            if ticket in self._active:
                self._active.discard(ticket)
                ticket._state = "cancelled"
                cancelled_before_start = False
            else:
                cancelled_before_start = self._drop_from_queue_locked(ticket)
                ticket._state = "cancelled"
        self._request_pump()
        return cancelled_before_start

    def _drop_from_queue_locked(self, ticket: BackgroundWorkerTicket) -> bool:
        for entry in tuple(self._queue):
            if entry[0] is ticket:
                self._queue.remove(entry)
                return True
        return False

    def _occupy_locked(self, ticket: BackgroundWorkerTicket) -> None:
        ticket._state = "active"
        ticket._started_at = time.monotonic()
        self._active.add(ticket)

    def _reclaim_stale_slots_locked(self) -> None:
        """Возвращает слоты, за которыми уже никто не считает.

        Гейт живёт всё время работы приложения, поэтому не может полагаться
        только на finished-сигналы: потерянный сигнал или снесённая вместе со
        страницей задача иначе заблокировали бы очередь навсегда.
        """
        if not self._active:
            return
        now = time.monotonic()
        max_age_sec = max(0.0, LONG_RUNNING_SLOT_RELEASE_MS / 1000)
        for ticket in tuple(self._active):
            if ticket.slot_is_stale(now=now, max_age_sec=max_age_sec):
                self._active.discard(ticket)
                ticket._state = "long_running"

    def _run_start(self, ticket: BackgroundWorkerTicket, start: Callable[[], None]) -> None:
        try:
            start()
        except Exception:
            # Не стартовавший воркер не должен занимать слот навсегда.
            self.release(ticket)
            raise
        self._schedule_long_running_release(ticket)

    def _schedule_long_running_release(self, ticket: BackgroundWorkerTicket) -> None:
        if LONG_RUNNING_SLOT_RELEASE_MS <= 0:
            return
        try:
            QTimer.singleShot(
                LONG_RUNNING_SLOT_RELEASE_MS,
                lambda: self._release_long_running(ticket),
            )
        except Exception:
            pass

    def _release_long_running(self, ticket: BackgroundWorkerTicket) -> None:
        """Отпускает слот у воркера, который работает слишком долго.

        Сам воркер продолжает выполняться: снимается только его право
        блокировать очередь. Повторный ``release`` по finished уже безвреден.
        """
        with self._lock:
            if ticket not in self._active:
                return
            self._active.discard(ticket)
            ticket._state = "long_running"
        self._request_pump()

    def _request_pump(self) -> None:
        try:
            self._pump_requested.emit()
        except RuntimeError:
            # Гейт уже удаляется — очередь всё равно никого не запустит.
            pass

    def _pump(self) -> None:
        while True:
            with self._lock:
                self._reclaim_stale_slots_locked()
                if not self._queue or len(self._active) >= self._limit:
                    return
                ticket, start = self._queue.popleft()
                if ticket.is_finished():
                    continue
                self._occupy_locked(ticket)
            self._run_start(ticket, start)


_GATE: BackgroundWorkerGate | None = None


def background_worker_gate() -> BackgroundWorkerGate:
    """Возвращает общий гейт приложения (создаётся лениво)."""
    global _GATE
    if _GATE is None:
        _GATE = BackgroundWorkerGate()
    return _GATE


def reset_background_worker_gate(gate: BackgroundWorkerGate | None = None) -> None:
    """Подменяет общий гейт. Нужно тестам, живому коду — нет."""
    global _GATE
    _GATE = gate


__all__ = [
    "BACKGROUND_WORKER_LIMIT",
    "LONG_RUNNING_SLOT_RELEASE_MS",
    "BackgroundWorkerGate",
    "BackgroundWorkerTicket",
    "background_worker_gate",
    "reset_background_worker_gate",
]
