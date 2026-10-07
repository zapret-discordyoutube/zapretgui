"""Единый пульт «запустить / остановить Zapret».

Одна дверь для всех мест, где пользователь включает или выключает обход:
точка в карточке «Статус работы», метка статуса в заголовке окна и меню трея.
Пульт сам ничего не запускает — он вызывает готовые команды runtime_feature,
а текущую фазу читает из общего UI-store. Логика «подождать, пока движок
будет готов» и «сначала остановить подбор стратегии» живёт только здесь.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from log.log import log


RUNTIME_START_RETRY_MS = 250
RUNTIME_START_MAX_RETRIES = 24
RUNTIME_START_CONFLICT_STOP_MAX_RETRIES = 240

ACTIVE_LAUNCH_PHASES = frozenset({"autostart_pending", "starting", "running"})
BUSY_LAUNCH_PHASES = frozenset({"autostart_pending", "starting", "stopping"})
KNOWN_LAUNCH_PHASES = frozenset({"autostart_pending", "starting", "running", "stopping", "failed", "stopped"})


def normalize_launch_phase(phase: Any, running: bool = False) -> str:
    """Приводит фазу из store к одному из известных значений."""
    text = str(phase or "").strip().lower()
    if text in KNOWN_LAUNCH_PHASES:
        return text
    return "running" if bool(running) else "stopped"


def launch_phase_from_state(state) -> str:
    return normalize_launch_phase(
        getattr(state, "launch_phase", ""),
        bool(getattr(state, "launch_running", False)),
    )


# Цвет точки состояния — одинаковый в карточке, в заголовке окна и в трее.
PHASE_COLOR_RUNNING = "#6ccb5f"
PHASE_COLOR_BUSY = "#f5a623"
PHASE_COLOR_FAILED = "#ff6b6b"


def phase_color(phase: str) -> str | None:
    """Цвет точки для фазы; None — остановлен (точки нет или она серая)."""
    normalized = normalize_launch_phase(phase)
    if normalized == "running":
        return PHASE_COLOR_RUNNING
    if normalized in BUSY_LAUNCH_PHASES:
        return PHASE_COLOR_BUSY
    if normalized == "failed":
        return PHASE_COLOR_FAILED
    return None


def mode_label_for_launch_method(method: str) -> str:
    from settings.mode import is_orchestra_launch_method, is_zapret1_launch_method, is_zapret2_launch_method

    if is_zapret2_launch_method(method):
        return "Zapret 2"
    if is_zapret1_launch_method(method):
        return "Zapret 1"
    if is_orchestra_launch_method(method):
        return "Оркестратор"
    return "Zapret"


def toggle_action_for_phase(phase: str) -> str:
    """Что сделает переключатель в этой фазе: "start", "stop" или "" (ждать)."""
    normalized = normalize_launch_phase(phase)
    if normalized == "stopping":
        return ""
    if normalized in ACTIVE_LAUNCH_PHASES:
        return "stop"
    return "start"


class LaunchControl(QObject):
    """Пуск, остановка, перезапуск и выход — одинаково из любого места программы."""

    # (идёт подготовка, текст) — пока движок ещё не готов к запуску.
    preparingChanged = pyqtSignal(bool, str)

    def __init__(
        self,
        *,
        runtime_feature,
        ui_state_store,
        request_exit: Callable[..., Any],
        stop_conflicting_checks: Callable[[], bool] | None = None,
        set_status: Callable[[str], Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._runtime = runtime_feature
        self._store = ui_state_store
        self._stop_conflicting_checks = stop_conflicting_checks
        self._set_status = set_status
        self._request_exit = request_exit
        self._retry_pending = False
        self._retry_count = 0
        self._retry_reason = "runtime"
        self._preparing_text = ""

    # ---- состояние ------------------------------------------------------

    def phase(self) -> str:
        try:
            return launch_phase_from_state(self._store.snapshot())
        except Exception:
            return "stopped"

    def is_preparing(self) -> bool:
        return self._retry_pending

    def preparing_text(self) -> str:
        return self._preparing_text if self._retry_pending else ""

    # ---- действия -------------------------------------------------------

    def toggle(self) -> str:
        """Переключает обход и возвращает выполненное действие ("start"/"stop"/"")."""
        if self._retry_pending:
            return ""
        action = toggle_action_for_phase(self.phase())
        if action == "start":
            self.start()
        elif action == "stop":
            self.stop()
        return action

    def start(self) -> None:
        if self._retry_pending:
            return
        if self._conflicting_checks_running():
            self._queue_retry(
                "Останавливаем подбор стратегии перед запуском Zapret...",
                reason="conflict_stop",
            )
            return
        if not self._runtime_available():
            self._queue_retry()
            return
        self._runtime.start()

    def stop(self) -> None:
        self._cancel_retry()
        self._runtime.stop()

    def restart(self) -> None:
        if self._retry_pending:
            return
        if normalize_launch_phase(self.phase()) != "running":
            self.start()
            return
        self._runtime.restart()

    def stop_and_exit(self) -> None:
        log("Остановка winws и закрытие программы...", "INFO")
        self._cancel_retry()
        self._request_exit(stop_dpi=True)

    # ---- ожидание готовности движка -----------------------------------

    def _runtime_available(self) -> bool:
        try:
            return bool(self._runtime.is_available())
        except Exception:
            return False

    def _conflicting_checks_running(self) -> bool:
        if not callable(self._stop_conflicting_checks):
            return False
        try:
            return bool(self._stop_conflicting_checks())
        except Exception:
            return False

    def _emit_status(self, message: str) -> None:
        if callable(self._set_status):
            try:
                self._set_status(message)
            except Exception:
                pass

    def _set_preparing(self, active: bool, text: str = "") -> None:
        self._preparing_text = str(text or "") if active else ""
        self.preparingChanged.emit(bool(active), self._preparing_text)

    def _queue_retry(self, message: str = "Подготовка запуска...", *, reason: str = "runtime") -> None:
        if self._retry_pending:
            return
        message = str(message or "").strip() or "Подготовка запуска..."
        self._retry_pending = True
        self._retry_count = 0
        self._retry_reason = str(reason or "runtime")
        self._set_preparing(True, message)
        self._emit_status(message)
        QTimer.singleShot(RUNTIME_START_RETRY_MS, self._retry_start)

    def _cancel_retry(self) -> None:
        if not self._retry_pending:
            return
        self._retry_pending = False
        self._set_preparing(False)

    def _finish_retry(self, status: str = "") -> None:
        self._retry_pending = False
        self._set_preparing(False)
        if status:
            self._emit_status(status)

    def _retry_start(self) -> None:
        if not self._retry_pending:
            return

        if self._conflicting_checks_running():
            self._retry_count += 1
            if self._retry_count >= RUNTIME_START_CONFLICT_STOP_MAX_RETRIES:
                self._finish_retry(
                    "Подбор стратегии ещё останавливается. Дождитесь остановки и запустите Zapret снова."
                )
                return
            QTimer.singleShot(RUNTIME_START_RETRY_MS, self._retry_start)
            return

        if self._runtime_available():
            self._finish_retry()
            self._runtime.start()
            return

        self._retry_count += 1
        if self._retry_count >= RUNTIME_START_MAX_RETRIES:
            self._finish_retry("Запуск ещё не готов. Попробуйте ещё раз через пару секунд.")
            return

        QTimer.singleShot(RUNTIME_START_RETRY_MS, self._retry_start)


def build_launch_control(
    *,
    runtime_feature,
    ui_state_store,
    request_exit,
    stop_conflicting_checks=None,
    set_status=None,
    parent=None,
) -> LaunchControl:
    return LaunchControl(
        runtime_feature=runtime_feature,
        ui_state_store=ui_state_store,
        stop_conflicting_checks=stop_conflicting_checks,
        set_status=set_status,
        request_exit=request_exit,
        parent=parent,
    )


__all__ = [
    "ACTIVE_LAUNCH_PHASES",
    "BUSY_LAUNCH_PHASES",
    "LaunchControl",
    "build_launch_control",
    "launch_phase_from_state",
    "mode_label_for_launch_method",
    "normalize_launch_phase",
    "phase_color",
    "toggle_action_for_phase",
]
