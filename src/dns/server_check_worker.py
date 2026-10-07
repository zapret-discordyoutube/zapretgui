"""Фоновая задача вкладки «DNS-серверы».

Получает готовое действие DNS-слоя (callable из фасада), выполняет его вне
UI-потока и отдаёт результат сигналами с request_id. Виджеты не трогает.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class ServerCheckWorker(QThread):
    """stage(request_id, отчёт) — по мере готовности адресов; completed — итог."""

    stage = pyqtSignal(int, object)
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, *, run_server_check: Callable[..., Any], parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._run_server_check = run_server_check
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def run(self) -> None:
        try:
            report = self._run_server_check(
                on_progress=lambda partial: self.stage.emit(self._request_id, partial),
                should_stop=self.is_stop_requested,
            )
        except Exception as exc:
            log(f"ServerCheckWorker: ошибка проверки DNS-серверов: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.completed.emit(self._request_id, report)
