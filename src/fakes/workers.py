"""Фоновые задачи страницы «Фейки»: чтение реестра, добавление и удаление."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class FakesTaskWorker(QThread):
    """Выполняет одну задачу вне GUI-потока и сообщает результат сигналом.

    ``loaded(request_id, result)`` — задача выполнена;
    ``failed(request_id, message)`` — понятная причина ошибки.
    """

    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, *, task, task_name: str, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._task = task
        self._task_name = str(task_name or "задача")

    def run(self) -> None:
        try:
            result = self._task()
        except ValueError as exc:
            # UserFakeError: причина уже сформулирована для пользователя.
            self.failed.emit(self._request_id, str(exc) or type(exc).__name__)
            return
        except Exception as exc:
            log(f"Фейки: {self._task_name} не выполнено: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc) or type(exc).__name__)
            return
        self.loaded.emit(self._request_id, result)


__all__ = ["FakesTaskWorker"]
