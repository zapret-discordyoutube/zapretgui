"""Состояние окна обновления, которое живёт дольше самого окна.

Окно обновления можно закрыть кнопкой «Скрыть» посреди загрузки и открыть
снова со страницы «Серверы». Проектные диалоги после закрытия заново не
показываются, поэтому окно каждый раз создаётся новым, а всё, что нужно
показать — предложение, ход загрузки, ошибка, — хранится здесь.

``UpdateFlow`` ничего не скачивает сам: загрузкой владеет
``UpdateInstallService``, страница передаёт сюда его сигналы.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, pyqtSignal

from updater.ui import plans

PHASE_IDLE = "idle"
PHASE_OFFER = "offer"
PHASE_DOWNLOADING = "downloading"
PHASE_INSTALLING = "installing"
PHASE_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UpdateOffer:
    version: str
    current_version: str
    source: str = ""
    url: str = ""
    # ({"version", "notes", "published_at", "url"}, ...), от новых к старым.
    history: tuple = ()


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    percent: int = 0
    done_bytes: int = 0
    total_bytes: int = 0
    known_size: bool = False
    size_text: str = ""
    speed_text: str = ""
    eta_text: str = ""
    stage_text: str = ""
    error_text: str = ""


@dataclass(slots=True)
class _SpeedState:
    last_speed_time: float = 0.0
    last_speed_bytes: int = 0
    smoothed_speed: float = 0.0
    speed_kb: float | None = None
    eta_seconds: float | None = None


class UpdateFlow(QObject):
    """Предложение и ход обновления. ``changed`` — при любом изменении."""

    changed = pyqtSignal()

    def __init__(self, *, language: str = "ru", parent=None) -> None:
        super().__init__(parent)
        self._language = str(language or "ru")
        self._phase = PHASE_IDLE
        self._offer: UpdateOffer | None = None
        self._progress = DownloadProgress()
        self._speed = _SpeedState()

    # --- чтение ---------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def offer(self) -> UpdateOffer | None:
        return self._offer

    @property
    def progress(self) -> DownloadProgress:
        return self._progress

    @property
    def is_busy(self) -> bool:
        return self._phase in {PHASE_DOWNLOADING, PHASE_INSTALLING}

    def set_language(self, language: str) -> None:
        self._language = str(language or "ru")

    # --- переходы -------------------------------------------------------------

    def set_offer(self, offer: UpdateOffer) -> bool:
        """Новое предложение. Во время загрузки не меняется: False."""
        if self.is_busy:
            return False
        self._offer = offer
        self._phase = PHASE_OFFER
        self._progress = DownloadProgress()
        self.changed.emit()
        return True

    def clear_offer(self) -> None:
        if self.is_busy:
            return
        self._offer = None
        self._phase = PHASE_IDLE
        self._progress = DownloadProgress()
        self.changed.emit()

    def start_download(self) -> None:
        now = time.time()
        self._phase = PHASE_DOWNLOADING
        self._speed = _SpeedState(last_speed_time=now)
        self._progress = DownloadProgress(
            stage_text=plans.update_flow_text(self._language, "stage.preparing", "Подготовка к загрузке…"),
        )
        self.changed.emit()

    def on_stage(self, text: str) -> None:
        if not self.is_busy:
            return
        message = str(text or "").strip()
        if message:
            self._progress = replace(self._progress, stage_text=message)
            self.changed.emit()

    def on_progress(self, percent: int, done_bytes: int, total_bytes: int) -> None:
        if self._phase != PHASE_DOWNLOADING:
            return
        plan = plans.build_changelog_progress_plan(
            percent=int(percent),
            done_bytes=int(done_bytes),
            total_bytes=int(total_bytes),
            last_speed_time=self._speed.last_speed_time,
            last_speed_bytes=self._speed.last_speed_bytes,
            smoothed_speed=self._speed.smoothed_speed,
            download_speed_kb=self._speed.speed_kb,
            download_eta_seconds=self._speed.eta_seconds,
            language=self._language,
            now=time.time(),
            progress_bar_visible=self._progress.known_size,
        )
        self._speed = _SpeedState(
            last_speed_time=plan.last_speed_time,
            last_speed_bytes=plan.last_speed_bytes,
            smoothed_speed=plan.smoothed_speed,
            speed_kb=plan.download_speed_kb,
            eta_seconds=plan.download_eta_seconds,
        )
        self._progress = replace(
            self._progress,
            percent=plan.progress_value,
            done_bytes=plan.download_done_bytes,
            total_bytes=plan.download_total_bytes,
            known_size=plan.show_progress_bar,
            size_text=plan.version_text,
            speed_text=plan.speed_label_text,
            eta_text=plan.eta_label_text,
        )
        self.changed.emit()

    def on_downloaded(self) -> None:
        if self._phase != PHASE_DOWNLOADING:
            return
        self._phase = PHASE_INSTALLING
        self._progress = replace(
            self._progress,
            percent=100,
            known_size=True,
            speed_text="",
            eta_text="",
            stage_text=plans.update_flow_text(
                self._language,
                "stage.installer_starting",
                "Запускаем установщик, программа закроется",
            ),
        )
        self.changed.emit()

    def on_failed(self, error: str) -> None:
        if not self.is_busy:
            return
        self._phase = PHASE_FAILED
        self._progress = replace(self._progress, error_text=str(error or "").strip())
        self.changed.emit()


__all__ = [
    "DownloadProgress",
    "PHASE_DOWNLOADING",
    "PHASE_FAILED",
    "PHASE_IDLE",
    "PHASE_INSTALLING",
    "PHASE_OFFER",
    "UpdateFlow",
    "UpdateOffer",
]
