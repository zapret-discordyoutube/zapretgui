"""Фоновый поток вкладки «Диагностика».

Сам ничего не проверяет: запускает ``diagnostics.engine`` и пересылает его
строки в окно и в файл ``connection_test_temp.log`` (этот файл потом кладётся
в архив для обращения в поддержку).
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QObject, pyqtSignal

from config.runtime_layout import APPLICATION_PATHS

LOGS_FOLDER = str(APPLICATION_PATHS.logs_dir)


class ConnectionTestWorker(QObject):
    """Рабочий поток для выполнения тестов соединения."""

    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, test_type="all"):
        super().__init__()
        self.test_type = test_type
        self.log_filename = os.path.join(LOGS_FOLDER, "connection_test_temp.log")
        self._stop_requested = False
        self._logger = logging.Logger("connection_test.worker", level=logging.INFO)
        self._logger.propagate = False
        self._file_handler = None

    def _open_logger(self) -> None:
        """Открывает файл уже внутри фонового потока диагностики."""
        self._close_logger()
        os.makedirs(LOGS_FOLDER, exist_ok=True)
        handler = logging.FileHandler(self.log_filename, "w", "utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s", "%Y-%m-%d %H:%M:%S"))
        self._logger.addHandler(handler)
        self._file_handler = handler

    def _close_logger(self) -> None:
        handler = self._file_handler
        if handler is None:
            return
        try:
            self._logger.removeHandler(handler)
        except Exception:
            pass
        try:
            handler.close()
        except Exception:
            pass
        self._file_handler = None

    def stop_gracefully(self):
        """Мягкая остановка: движок заметит флаг и сразу снимет сетевые запросы."""
        self._stop_requested = True

    def stop(self) -> None:
        self.stop_gracefully()

    def is_stop_requested(self):
        return self._stop_requested

    def log_message(self, message, *, allow_after_stop: bool = False):
        """Записывает строку в лог и отправляет её в окно."""
        if allow_after_stop or not self._stop_requested:
            self._logger.info(message)
            self.update_signal.emit(message)

    def release_resources(self) -> None:
        """Освобождает файловые ресурсы worker'а."""
        self._close_logger()

    def run(self):
        try:
            self._open_logger()
            from diagnostics.engine import run_connection_test

            run_connection_test(
                self.test_type,
                emit=self.log_message,
                should_stop=self.is_stop_requested,
            )
            if self._stop_requested:
                self.log_message("⚠️ Тестирование остановлено пользователем", allow_after_stop=True)
            else:
                self.log_message(f"Лог сохранён в файле {os.path.abspath(self.log_filename)}")
        except Exception as e:
            if not self._stop_requested:
                self.log_message(f"❌ Критическая ошибка в тесте: {e}")
        finally:
            self._close_logger()
            self.finished_signal.emit()
            self.finished.emit()
