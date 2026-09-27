from __future__ import annotations

"""Сервис ручной проверки обновлений для страницы «Серверы».

Живёт в главном потоке, работу отдаёт одному фоновому потоку на проверку.
Правила:

* одновременно идёт только одна проверка, и пока она не закончилась —
  вместе с возвратом DPI, — новую не начать;
* номер проверки у общего координатора закрывается всегда: результатом,
  ошибкой или отметкой «остановлена». Иначе на экране навсегда осталось бы
  «Проверка…», а стартовая проверка больше не началась бы;
* строки и итог старой проверки не принимаются.

Поток фоновый (daemon): закрытие программы не ждёт медленную сеть. Общая
очередь фоновых задач программы тут не подходит: в ней одновременно идут
две задачи, а проверка при недоступных серверах длится до ~40 с и заняла
бы очередь других частей программы.
"""

import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from config.build_info import APP_VERSION, CHANNEL
from log.log import log

from ..dpi_guard import DpiGuard
from ..versions import compare_versions
from .flow import CheckOutcome, run_update_check


def outcome_to_result(outcome: CheckOutcome, *, app_version: str = APP_VERSION) -> dict:
    """Итог проверки в формате координатора."""
    if outcome.release is None:
        return {"has_update": False, "version": "", "release_notes": "", "error": outcome.error}
    release = outcome.release
    version = str(release.get("version") or "")
    try:
        has_update = compare_versions(app_version, version) < 0
    except ValueError as exc:
        return {"has_update": False, "version": "", "release_notes": "", "error": f"Некорректная версия выпуска: {exc}"}
    return {
        "has_update": has_update,
        "version": version if has_update else app_version,
        "release_notes": str(release.get("release_notes") or "") if has_update else "",
        "release_source": str(release.get("source") or ""),
        "error": None,
    }


class UpdateCheckService(QObject):
    server_status = pyqtSignal(str, dict)
    busy_changed = pyqtSignal(bool)

    _task_row = pyqtSignal(int, str, dict)
    _task_done = pyqtSignal(int, object)
    # Запуск DPI обратно выполняется в главном потоке: он создаёт потоки
    # и таймеры Qt, которые в обычном фоновом потоке не сработали бы.
    _task_restart_dpi = pyqtSignal()

    def __init__(
        self,
        *,
        updater_feature,
        runtime_actions,
        run_check: Callable[..., CheckOutcome] = run_update_check,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._updater_feature = updater_feature
        self._runtime_actions = runtime_actions
        self._run_check = run_check
        self._generation = 0
        self._token: int | None = None
        self._busy = False
        self._shutting_down = False
        self._task_row.connect(self._on_task_row)
        self._task_done.connect(self._on_task_done)
        self._task_restart_dpi.connect(self._on_task_restart_dpi)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def start(self, *, language: str = "ru") -> bool:
        """Начинает проверку. False — если проверка уже идёт.

        ``language`` — язык текстов в строках таблицы.
        """
        if self._busy or self._shutting_down:
            return False
        token = self._updater_feature.begin_update_check(source="manual")
        if token is None:
            return False
        self._token = int(token)
        self._generation += 1
        generation = self._generation
        self._set_busy(True)

        dpi = DpiGuard(
            is_any_running=self._runtime_actions.is_any_running,
            shutdown_sync=self._runtime_actions.shutdown_sync,
            is_available=self._runtime_actions.is_available,
            restart=lambda: emit(self._task_restart_dpi),
            allow_restore=lambda: not self._shutting_down,
        )
        language = str(language or "ru")

        def emit(signal, *args) -> None:
            try:
                signal.emit(*args)
            except RuntimeError:
                # Сервис уже удалён вместе со страницей: сообщать некому.
                pass

        def run() -> None:
            try:
                outcome = self._run_check(
                    CHANNEL,
                    language=language,
                    emit_row=lambda name, status: emit(self._task_row, generation, name, dict(status)),
                    dpi=dpi,
                )
            except Exception as exc:
                log(f"Проверка обновлений упала: {exc}", "❌ ERROR")
                outcome = CheckOutcome(None, str(exc) or type(exc).__name__)
            emit(self._task_done, generation, outcome)

        threading.Thread(target=run, name="update-check", daemon=True).start()
        return True

    def shutdown(self) -> None:
        """Закрытие страницы или программы: DPI обратно не запускается."""
        self._shutting_down = True
        if self._busy:
            self._finish({"has_update": False, "skipped": True, "skip_reason": "Проверка остановлена"})
        self._generation += 1
        self._set_busy(False)

    def _on_task_row(self, generation: int, name: str, status: dict) -> None:
        if generation == self._generation:
            self.server_status.emit(name, status)

    def _on_task_done(self, generation: int, outcome: CheckOutcome) -> None:
        if generation != self._generation:
            return
        self._finish(outcome_to_result(outcome))
        self._set_busy(False)

    def _finish(self, result: dict) -> None:
        token, self._token = self._token, None
        if token is not None:
            self._updater_feature.finish_update_check(result, source="manual", token=token)

    def _on_task_restart_dpi(self) -> None:
        if self._shutting_down:
            return
        try:
            self._runtime_actions.restart()
        except Exception as exc:
            log(f"Не удалось запустить DPI обратно: {exc}", "❌ ERROR")

    def _set_busy(self, busy: bool) -> None:
        if self._busy != busy:
            self._busy = busy
            self.busy_changed.emit(busy)


__all__ = ["UpdateCheckService", "outcome_to_result"]
