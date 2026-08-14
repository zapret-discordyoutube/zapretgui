"""QThread-воркер скачивания пресета по ссылке для диалога импорта."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log


class PresetImportDownloadWorker(QThread):
    succeeded = pyqtSignal(int, object)  # request_id, DownloadedPresetFile
    failed = pyqtSignal(int, str, str)  # request_id, kind, detail

    def __init__(self, request_id: int, url: str, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._url = str(url or "").strip()
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        from presets.preset_url_import import PresetUrlDownloadError, download_preset_from_url

        try:
            result = download_preset_from_url(
                self._url,
                cancel_cb=lambda: self._cancel_requested,
            )
        except PresetUrlDownloadError as exc:
            if exc.kind != "cancelled":
                log(f"Скачивание пресета по ссылке не удалось ({exc.kind}): {exc.detail}", "ERROR")
            self.failed.emit(self._request_id, exc.kind, exc.detail)
            return
        except Exception as exc:  # неожиданные ошибки — тоже в диалог, не в crash
            log(f"Скачивание пресета по ссылке: неожиданная ошибка: {exc}", "ERROR")
            self.failed.emit(self._request_id, "network", str(exc))
            return
        self.succeeded.emit(self._request_id, result)
