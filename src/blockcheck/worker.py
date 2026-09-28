"""Фоновый поток проверки BlockCheck.

Сам ничего не проверяет: запускает ``diagnostics.engine.run_blockcheck``,
пишет каждую строку отчёта в журнал ``blockcheck_run_*.log`` (его потом
забирает обращение в поддержку) и отдаёт странице готовый итог.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class BlockcheckWorker(QObject):
    """Проверка BlockCheck в QThread, которым владеет общий runtime страницы."""

    run_log_started = pyqtSignal(object)
    log_message = pyqtSignal(str)
    # Итог проверки (словарь из run_blockcheck), None — если её остановили,
    # {"failed": True, "error": …} — если проверка упала.
    finished = pyqtSignal(object)

    def __init__(
        self,
        scope: str = "main",
        user_domains: list[str] | None = None,
        *,
        start_run_log: Callable[[str, list[str]], object],
        append_run_log: Callable[[str | None, str], None],
        close_run_log: Callable[[str | None], None],
        parent=None,
    ):
        super().__init__(parent)
        self._scope = str(scope or "main")
        self._user_domains = list(user_domains or [])
        self._start_run_log = start_run_log
        self._append_run_log_action = append_run_log
        self._close_run_log_action = close_run_log
        self._cancelled = False
        self._running = False
        self._run_log_file = None

    def run(self):
        # Флаг «Стоп» здесь не сбрасывается: проверку могли остановить, пока
        # она ждала очереди фоновых задач. Обработчик создаётся на каждую
        # проверку заново.
        self._running = True
        report = None
        try:
            from diagnostics.engine import run_blockcheck

            log_state = self._start_run_log(self._scope, list(self._user_domains))
            self._run_log_file = log_state.path
            self.run_log_started.emit(log_state.path)
            if not log_state.created:
                logger.warning("Failed to create blockcheck run log")

            report = run_blockcheck(
                self._scope,
                user_domains=self._user_domains,
                emit=self._emit,
                should_stop=self.is_cancelled,
            )
            if isinstance(report, dict) and report.get("stopped"):
                report = None
        except Exception as e:
            logger.exception("BlockcheckWorker crashed")
            self._emit(f"❌ Проверка упала: {e}")
            report = {"failed": True, "error": str(e)}
        finally:
            try:
                self._close_run_log_action(self._run_log_file)
            except Exception:
                pass
            self._running = False
        self.finished.emit(report)

    def stop(self):
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    def _emit(self, message: str) -> None:
        try:
            self._append_run_log_action(self._run_log_file, message)
        except Exception:
            pass
        self.log_message.emit(message)
