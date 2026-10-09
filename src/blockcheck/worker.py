"""Фоновый поток проверки BlockCheck.

Сам ничего не проверяет: запускает ``diagnostics.engine.run_blockcheck``,
копит строки отчёта и в конце кладёт их вместе с данными в один файл ``blockcheck_run_*.json`` (его потом
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
    # Ход проверки: шаг, сколько готово и сколько всего (см. engine.PROGRESS_STEPS).
    progress = pyqtSignal(str, int, int)
    # Отчёт по ходу проверки: тот же словарь, что придёт в ``finished``, но пока неполный
    # (``report["partial"]`` истинно). Приходит после каждого проверенного сервиса и раздела.
    partial = pyqtSignal(object)

    def __init__(
        self,
        scope: str = "main",
        user_domains: list[str] | None = None,
        *,
        report_path: Callable[[str], str],
        save_report: Callable[[dict, str | None], str],
        load_geo_sites: Callable[[], object] | None = None,
        remember_run: Callable[[dict, str | None], dict] | None = None,
        check_dns_servers: Callable[..., dict] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._scope = str(scope or "main")
        self._user_domains = list(user_domains or [])
        self._report_path = report_path
        self._save_report = save_report
        # Текст отчёта копится здесь и уходит в тот же файл, что и данные: файл у проверки один.
        self._lines: list[str] = []
        self._last_partial: dict | None = None
        self._load_geo_sites = load_geo_sites
        self._remember_run = remember_run
        self._check_dns_servers = check_dns_servers
        self._cancelled = False
        self._running = False
        self._run_log_file = None

    def _geo_service_lookup(self):
        """Поиск «адрес → гео-сервис» из каталога hosts; без каталога — None."""
        if self._load_geo_sites is None:
            return None
        try:
            return self._load_geo_sites().service_for
        except Exception:
            logger.exception("Failed to load geo sites for blockcheck")
            return None

    def run(self):
        # Флаг «Стоп» здесь не сбрасывается: проверку могли остановить, пока
        # она ждала очереди фоновых задач. Обработчик создаётся на каждую
        # проверку заново.
        self._running = True
        report = None
        # Пока идёт проверка, программа сама не обновляется: перезапуск
        # оборвал бы её.
        from core.runtime.long_tasks import begin_long_task, end_long_task

        long_task = begin_long_task("blockcheck")
        try:
            from diagnostics.engine import run_blockcheck

            self._run_log_file = self._report_path(self._scope)

            report = run_blockcheck(
                self._scope,
                user_domains=self._user_domains,
                emit=self._emit,
                should_stop=self.is_cancelled,
                geo_service_for=self._geo_service_lookup(),
                check_dns_servers=self._check_dns_servers,
                progress=self.progress.emit,
                partial=self._on_partial,
            )
            if isinstance(report, dict) and report.get("stopped"):
                self._save_unfinished(stopped=True)
                report = None
            if report is not None:
                report["text"] = list(self._lines)
                self._remember(report)
        except Exception as e:
            logger.exception("BlockcheckWorker crashed")
            self._emit(f"❌ Проверка упала: {e}")
            self._save_unfinished(failed=True, error=str(e))
            report = {"failed": True, "error": str(e)}
        finally:
            end_long_task(long_task)
            self._running = False
        self.finished.emit(report)

    def _on_partial(self, report: dict) -> None:
        self._last_partial = report
        self.partial.emit(report)

    def _save_unfinished(self, **marks) -> None:
        """Прерванная проверка тоже остаётся в файле: что успели узнать и текст до этого места."""
        try:
            document = dict(self._last_partial or {}, text=list(self._lines), **marks)
            if self._save_report(document, self._run_log_file):
                self.run_log_started.emit(self._run_log_file)
        except Exception:
            logger.exception("Failed to save unfinished blockcheck report")

    def _remember(self, report: dict) -> None:
        """Записывает прогон в историю и дописывает, что изменилось с прошлого раза.

        Сбой записи не должен стоить человеку результата проверки.
        """
        if self._remember_run is None:
            return
        try:
            note = self._remember_run(report, self._run_log_file) or {}
        except Exception:
            logger.exception("Failed to remember blockcheck run")
            return
        changes = [str(item) for item in note.get("changes") or ()]
        report["changes"] = changes
        report["previous_time"] = str(note.get("previous_time") or "")
        report["json_file"] = str(note.get("json_file") or "")
        if report["json_file"]:
            self.run_log_started.emit(report["json_file"])
        report["history"] = [dict(run) for run in note.get("history") or () if isinstance(run, dict)]
        # Сравнение «с Zapret и без» и строка об изменениях уже лежат в файле; здесь — только на экран.
        for line in note.get("lines") or ():
            self.log_message.emit(str(line))

    def stop(self):
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def is_running(self) -> bool:
        return bool(self._running)

    def _emit(self, message: str) -> None:
        self._lines.append(str(message))
        self.log_message.emit(message)
