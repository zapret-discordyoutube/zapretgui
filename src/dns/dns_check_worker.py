from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class DNSCheckWorker(QObject):
    """Worker для выполнения DNS проверки в отдельном потоке."""

    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, dict)
    finished = pyqtSignal()

    def __init__(self, request_id: int, *, run_dns_poisoning_check):
        super().__init__()
        self._request_id = int(request_id)
        self._run_dns_poisoning_check = run_dns_poisoning_check
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def run(self):
        try:
            results = self._run_dns_poisoning_check(
                log_callback=self.update_signal.emit,
                should_stop=self.is_stop_requested,
            )
            self.finished_signal.emit(self._request_id, results)
        except Exception as e:
            if not self._stop_requested:
                self.update_signal.emit(f"❌ Ошибка: {str(e)}")
            self.finished_signal.emit(self._request_id, {})
        self.finished.emit()


class DNSCheckSaveWorker(QThread):
    """Сохраняет отчёт DNS Check вне UI-потока."""

    saved = pyqtSignal(int, object)

    def __init__(self, request_id: int, *, file_path: str, plain_text: str, save_dns_check_results, parent=None):
        super().__init__(parent)
        self._request_id = int(request_id)
        self._file_path = str(file_path or "")
        self._plain_text = str(plain_text or "")
        self._save_dns_check_results = save_dns_check_results

    def run(self) -> None:
        plan = self._save_dns_check_results(
            file_path=self._file_path,
            plain_text=self._plain_text,
        )
        self.saved.emit(self._request_id, plan)
