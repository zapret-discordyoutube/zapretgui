"""Дорожка фоновой работы страницы: «одна задача за раз, последний запрос побеждает».

Страница просит дорожку что-то сделать (загрузить данные, применить DNS,
замерить скорость). Если задача уже идёт, новый запрос не запускает вторую
параллельно, а запоминается; из нескольких таких запросов выполнится только
последний, сразу после текущего. Результат устаревшей задачи (пока ждёт
новый запрос) страница не получает: он всё равно уже неактуален.

Воркер — QThread с сигналами `<result_signal>(request_id, result)` и,
по желанию, `failed(request_id, error)`.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QTimer

from ui.one_shot_worker_runtime import OneShotWorkerRuntime

_EMPTY = object()


class LatestWorkerLane:
    def __init__(
        self,
        *,
        name: str,
        create_worker: Callable[[int, Any], object],
        on_result: Callable[[Any, Any], None],
        on_error: Callable[[Any, str], None] | None = None,
        result_signal: str = "completed",
        log_fn: Callable[[str, str], None] | None = None,
        schedule: Callable[[int, Callable[[], None]], None] = QTimer.singleShot,
    ) -> None:
        self.name = name
        self.runtime = OneShotWorkerRuntime()
        self._create_worker = create_worker
        self._on_result = on_result
        self._on_error = on_error
        self._result_signal = result_signal
        self._log_fn = log_fn
        self._schedule = schedule
        self._pending: Any = _EMPTY
        self._start_scheduled = False
        self._closed = False
        self._payloads: dict[int, Any] = {}

    # ── состояние ───────────────────────────────────────────

    def is_busy(self) -> bool:
        return self._start_scheduled or self.runtime.is_running()

    def has_pending(self) -> bool:
        return self._pending is not _EMPTY

    # ── запросы ─────────────────────────────────────────────

    def request(self, payload: Any = None) -> None:
        if self._closed:
            return
        if self.is_busy():
            self._pending = payload
            return
        self._start(payload)

    def close(self) -> None:
        """Страница уходит: ничего нового не запускаем, результаты не отдаём."""
        self._closed = True
        self._pending = _EMPTY
        self._start_scheduled = False
        self._payloads.clear()
        self.runtime.stop(blocking=False, log_fn=self._log_fn, warning_prefix=self.name)
        self.runtime.cancel()

    # ── внутреннее ──────────────────────────────────────────

    def _start(self, payload: Any) -> None:
        def factory(request_id: int):
            self._payloads = {request_id: payload}
            return self._create_worker(request_id, payload)

        self.runtime.start_qthread_worker(
            worker_factory=factory,
            on_failed=self._handle_failed,
            on_finished=self._handle_finished,
            bind_worker=self._bind,
        )

    def _bind(self, worker) -> None:
        getattr(worker, self._result_signal).connect(self._handle_result)

    def _is_fresh(self, request_id: int) -> bool:
        return self.runtime.is_current(request_id, cleanup_in_progress=self._closed) and not self.has_pending()

    def _handle_result(self, request_id: int, result) -> None:
        if self._is_fresh(request_id):
            self._on_result(self._payloads.get(int(request_id)), result)

    def _handle_failed(self, request_id: int, error: str) -> None:
        if not self._is_fresh(request_id):
            return
        if self._log_fn is not None:
            self._log_fn(f"{self.name}: {error}", "WARNING")
        if self._on_error is not None:
            self._on_error(self._payloads.get(int(request_id)), str(error or ""))

    def _handle_finished(self, _worker) -> None:
        if self._closed or not self.has_pending() or self._start_scheduled:
            return
        # Следующий запуск — через оборот цикла событий: текущий QThread
        # должен до конца отработать свой finished.
        self._start_scheduled = True
        self._schedule(0, self._run_pending)

    def _run_pending(self) -> None:
        self._start_scheduled = False
        payload, self._pending = self._pending, _EMPTY
        if self._closed or payload is _EMPTY:
            return
        self._start(payload)


__all__ = ["LatestWorkerLane"]
