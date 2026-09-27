from __future__ import annotations

"""Временная остановка DPI на время проверки или скачивания обновления.

Единственное место, которое останавливает DPI ради обновления и запускает
его обратно. Правила:

* запускаем обратно только то, что остановили сами;
* после успешного запуска установщика обратно не запускаем: программа
  закрывается, и winws остался бы без хозяина;
* при выходе из программы тоже не запускаем (``allow_restore``).

Вызывается из фонового потока: остановка DPI ждёт завершения процессов.
"""

from collections.abc import Callable
from typing import Any

from log.log import log


UPDATE_LOG_LEVEL = "🔁 UPDATE"


class DpiStopError(RuntimeError):
    """DPI не остановился."""


class DpiGuard:
    def __init__(
        self,
        *,
        is_any_running: Callable[[], bool],
        shutdown_sync: Callable[..., Any],
        is_available: Callable[[], bool],
        restart: Callable[[], Any],
        on_stopped: Callable[[], None] | None = None,
        allow_restore: Callable[[], bool] = lambda: True,
    ) -> None:
        self._is_any_running = is_any_running
        self._shutdown_sync = shutdown_sync
        self._is_available = is_available
        self._restart = restart
        self._on_stopped = on_stopped
        self._allow_restore = allow_restore
        self._stopped_by_us = False

    @property
    def stopped_by_us(self) -> bool:
        return self._stopped_by_us

    def is_running(self) -> bool:
        try:
            return bool(self._is_any_running())
        except Exception:
            return False

    def stop(self, *, reason: str, update_runtime_state: bool = True) -> bool:
        """Останавливает DPI, если он работает. True — если остановили мы.

        ``update_runtime_state=False`` нужен установке: состояние помечается
        остановленным через ``on_stopped`` в главном потоке, а не синхронизацией
        из фонового.
        """
        if not self.is_running():
            return False
        log(f"Остановка DPI для обновления ({reason})", UPDATE_LOG_LEVEL)
        result = self._shutdown_sync(
            reason=str(reason),
            include_cleanup=True,
            update_runtime_state=bool(update_runtime_state),
        )
        # Даже частичная остановка — повод потом запустить DPI обратно.
        self._stopped_by_us = True
        if bool(getattr(result, "still_running", False)):
            raise DpiStopError("DPI не остановился")
        if self._on_stopped is not None:
            self._on_stopped()
        return True

    def restore(self) -> None:
        """Запускает DPI обратно, если его останавливали мы. Повторный вызов безопасен."""
        if not self._stopped_by_us:
            return
        self._stopped_by_us = False
        try:
            if not self._allow_restore():
                log("DPI не запускается обратно: программа закрывается", UPDATE_LOG_LEVEL)
                return
            if not self._is_available():
                return
            log("Запуск DPI обратно после обновления", UPDATE_LOG_LEVEL)
            self._restart()
        except Exception as exc:
            log(f"Не удалось запустить DPI обратно: {exc}", "❌ ERROR")


__all__ = ["DpiGuard", "DpiStopError"]
