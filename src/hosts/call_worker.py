from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class HostsCallWorker(QThread):
    """Выполняет одну функцию hosts вне UI-потока и отдаёт её результат."""

    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id: int, call: Callable[[], object], *, name: str, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._call = call
        self._name = str(name)

    def run(self) -> None:
        try:
            result = self._call()
        except Exception as exc:
            log(f"Hosts ({self._name}): {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, result)


__all__ = ["HostsCallWorker"]
