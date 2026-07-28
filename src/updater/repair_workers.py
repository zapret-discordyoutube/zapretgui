from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class InstallationRepairWorker(QThread):
    """Фоновая починка поставки: проверка кэша, при необходимости загрузка."""

    loaded = pyqtSignal(int, bool, str, str)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        repair_installation,
        report=None,
        allow_download: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._repair_installation = repair_installation
        self._report = report
        self._allow_download = bool(allow_download)

    def run(self) -> None:
        try:
            outcome = self._repair_installation(
                self._report,
                allow_download=self._allow_download,
            )
        except Exception as exc:
            log(f"Восстановление поставки не удалось: {exc}", "❌ ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(
            self._request_id,
            bool(outcome.started),
            str(outcome.reason or ""),
            str(outcome.source or ""),
        )


__all__ = ["InstallationRepairWorker"]
