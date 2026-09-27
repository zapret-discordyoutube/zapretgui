from __future__ import annotations

"""Сервис установки обновления для страницы «Серверы».

Живёт в главном потоке, весь путь установки (``flow.run_update_install``)
отдаёт одному фоновому потоку с одним признаком отмены. Страница только
показывает этапы и прогресс.

После успешной передачи установщику программа закрывается штатно: выход
запрашивается в главном потоке и без остановки DPI — его уже остановил путь
установки.

Поток фоновый (daemon), как у проверки: скачивание длится минуты и не должно
ни занимать общую очередь фоновых задач, ни задерживать закрытие программы.
"""

import threading
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from log.log import log

from ..dpi_guard import DpiGuard
from .downloader import CancellationToken, UpdateCancelled
from .flow import run_update_install


class UpdateInstallService(QObject):
    stage_changed = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)
    downloaded = pyqtSignal()
    launched = pyqtSignal()
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)

    _task_stage = pyqtSignal(int, str)
    _task_progress = pyqtSignal(int, int, int, int)
    _task_dpi_stopped = pyqtSignal(int)
    _task_downloaded = pyqtSignal(int)
    _task_done = pyqtSignal(int, str)
    # Запуск DPI обратно выполняется в главном потоке: он создаёт потоки
    # и таймеры Qt, которые в обычном фоновом потоке не сработали бы.
    _task_restart_dpi = pyqtSignal()

    def __init__(
        self,
        *,
        runtime_actions,
        run_install: Callable[..., None] = run_update_install,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._runtime_actions = runtime_actions
        self._run_install = run_install
        self._generation = 0
        self._token: CancellationToken | None = None
        self._busy = False
        self._shutting_down = False
        self._task_stage.connect(self._on_task_stage)
        self._task_progress.connect(self._on_task_progress)
        self._task_dpi_stopped.connect(self._on_task_dpi_stopped)
        self._task_downloaded.connect(self._on_task_downloaded)
        self._task_done.connect(self._on_task_done)
        self._task_restart_dpi.connect(self._on_task_restart_dpi)

    @property
    def is_busy(self) -> bool:
        return self._busy

    def start(self, requested_version: str) -> bool:
        """Начинает установку. False — если она уже идёт."""
        if self._busy or self._shutting_down:
            return False
        self._generation += 1
        generation = self._generation
        token = CancellationToken()
        self._token = token
        self._set_busy(True)
        log(f"Запуск установки обновления v{requested_version}", "🔄 UPDATE")

        def emit(signal, *args) -> None:
            try:
                signal.emit(*args)
            except RuntimeError:
                pass

        dpi = DpiGuard(
            is_any_running=self._runtime_actions.is_any_running,
            shutdown_sync=self._runtime_actions.shutdown_sync,
            is_available=self._runtime_actions.is_available,
            restart=lambda: emit(self._task_restart_dpi),
            on_stopped=lambda: emit(self._task_dpi_stopped, generation),
            allow_restore=lambda: not self._shutting_down,
        )

        def run() -> None:
            error = ""
            try:
                self._run_install(
                    str(requested_version or ""),
                    token=token,
                    dpi=dpi,
                    on_stage=lambda text: emit(self._task_stage, generation, str(text)),
                    on_progress=lambda percent, done, total: emit(
                        self._task_progress, generation, int(percent), int(done), int(total)
                    ),
                    on_downloaded=lambda: emit(self._task_downloaded, generation),
                )
            except UpdateCancelled:
                error = "Обновление остановлено"
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                log(f"Обновление не установлено: {error}", "🔁❌ ERROR")
            emit(self._task_done, generation, error)

        threading.Thread(target=run, name="update-install", daemon=True).start()
        return True

    def cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()

    def shutdown(self) -> None:
        """Закрытие программы: работа останавливается, DPI обратно не запускается."""
        self._shutting_down = True
        self.cancel()
        self._generation += 1
        self._set_busy(False)

    def _on_task_stage(self, generation: int, text: str) -> None:
        if generation == self._generation and text:
            self.stage_changed.emit(text)

    def _on_task_progress(self, generation: int, percent: int, done: int, total: int) -> None:
        if generation == self._generation:
            self.progress.emit(percent, done, total)

    def _on_task_downloaded(self, generation: int) -> None:
        if generation == self._generation:
            self.downloaded.emit()

    def _on_task_dpi_stopped(self, _generation: int) -> None:
        # Остановка уже случилась: состояние отмечаем даже для устаревшей задачи.
        self._runtime_actions.mark_stopped()

    def _on_task_done(self, generation: int, error: str) -> None:
        if generation != self._generation:
            return
        self._token = None
        self._set_busy(False)
        if error:
            self.failed.emit(error)
            return
        log("Установщик запущен; приложение закрывается штатно", "🔁 UPDATE")
        self.launched.emit()
        QTimer.singleShot(0, lambda: self._runtime_actions.request_exit(stop_dpi=False))

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


__all__ = ["UpdateInstallService"]
