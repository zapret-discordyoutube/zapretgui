"""Наблюдатель за отзывчивостью интерфейса.

Заблокированный GUI-поток не оставляет следов: приложение просто перестаёт
рисовать, Windows показывает «программа не отвечает», а в логе — пустой
промежуток. Чтобы такие случаи диагностировались по обычному логу, а не
подключением профилировщика к чужому процессу, здесь измеряется задержка
event loop и при блокировке пишется стек GUI-потока.

Механика: таймер в GUI-потоке обновляет отметку времени, фоновый поток
сравнивает её с текущим временем. Разрыв больше порога — заморозка.
"""

from __future__ import annotations

import threading
import time
import traceback

from PyQt6.QtCore import QObject, QTimer

HEARTBEAT_INTERVAL_MS = 500
FREEZE_THRESHOLD_SECONDS = 2.0
# Пока интерфейс стоит, стек снимается повторно: длинная блокировка обычно
# проходит несколько стадий, и первая из них не самая интересная.
FREEZE_REPEAT_SECONDS = 10.0


def _format_gui_stack() -> str:
    """Стек GUI-потока на момент вызова (снимается из фонового потока)."""
    from ui.ui_thread_guard import gui_thread_id

    thread_id = gui_thread_id()
    if thread_id is None:
        return "GUI-поток не помечен"
    try:
        import sys

        frame = sys._current_frames().get(thread_id)
    except Exception as exc:
        return f"стек недоступен: {exc}"
    if frame is None:
        return "стек недоступен: поток не найден"
    try:
        lines = traceback.format_stack(frame)
    except Exception as exc:
        return f"стек недоступен: {exc}"
    return "".join(lines[-25:]).rstrip()


class UiFreezeWatchdog(QObject):
    """Измеряет задержку event loop и логирует блокировки интерфейса."""

    def __init__(
        self,
        *,
        heartbeat_interval_ms: int = HEARTBEAT_INTERVAL_MS,
        freeze_threshold_seconds: float = FREEZE_THRESHOLD_SECONDS,
        repeat_seconds: float = FREEZE_REPEAT_SECONDS,
        log_fn=None,
        stack_fn=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._heartbeat_interval_ms = max(50, int(heartbeat_interval_ms))
        self._freeze_threshold = max(0.2, float(freeze_threshold_seconds))
        self._repeat_seconds = max(0.2, float(repeat_seconds))
        self._log_fn = log_fn
        self._stack_fn = stack_fn or _format_gui_stack
        self._last_beat = time.monotonic()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._timer: QTimer | None = None
        self._thread: threading.Thread | None = None
        self._freeze_started_at: float | None = None
        self._last_report_at: float = 0.0
        self.longest_freeze_seconds = 0.0

    # ------------------------------------------------------------------
    # Жизненный цикл
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._timer is not None:
            return
        timer = QTimer(self)
        timer.setInterval(self._heartbeat_interval_ms)
        timer.timeout.connect(self.beat)
        timer.start()
        self._timer = timer
        self.beat()

        thread = threading.Thread(
            target=self._watch_loop,
            name="UiFreezeWatchdog",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        timer = self._timer
        self._timer = None
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Измерение
    # ------------------------------------------------------------------

    def beat(self) -> None:
        """Отметка живого event loop. Вызывается только в GUI-потоке."""
        with self._lock:
            self._last_beat = time.monotonic()

    def stall_seconds(self, now: float | None = None) -> float:
        with self._lock:
            last_beat = self._last_beat
        return max(0.0, (time.monotonic() if now is None else now) - last_beat)

    def check_once(self, now: float | None = None) -> float:
        """Одна проверка. Возвращает текущую задержку event loop."""
        moment = time.monotonic() if now is None else now
        stall = self.stall_seconds(moment)

        if stall >= self._freeze_threshold:
            if self._freeze_started_at is None:
                self._freeze_started_at = moment - stall
                self._last_report_at = moment
                self._report_freeze_started(stall)
            elif moment - self._last_report_at >= self._repeat_seconds:
                self._last_report_at = moment
                self._report_freeze_continues(stall)
            return stall

        if self._freeze_started_at is not None:
            duration = max(0.0, moment - self._freeze_started_at)
            self.longest_freeze_seconds = max(self.longest_freeze_seconds, duration)
            self._freeze_started_at = None
            self._report_freeze_ended(duration)
        return stall

    def _watch_loop(self) -> None:
        interval = self._heartbeat_interval_ms / 1000.0
        while not self._stop_event.wait(interval):
            try:
                self.check_once()
            except Exception:
                # Наблюдатель не имеет права уронить приложение.
                pass

    # ------------------------------------------------------------------
    # Отчёты
    # ------------------------------------------------------------------

    def _log(self, message: str, level: str) -> None:
        log_fn = self._log_fn
        if log_fn is None:
            try:
                from log.log import log as log_fn
            except Exception:
                return
        try:
            log_fn(message, level)
        except Exception:
            pass

    def _report_freeze_started(self, stall: float) -> None:
        self._log(
            f"Интерфейс не отвечает {stall:.1f}с. Стек GUI-потока:\n{self._stack_fn()}",
            "⚠ WARNING",
        )

    def _report_freeze_continues(self, stall: float) -> None:
        self._log(
            f"Интерфейс всё ещё не отвечает ({stall:.1f}с). Стек GUI-потока:\n{self._stack_fn()}",
            "⚠ WARNING",
        )

    def _report_freeze_ended(self, duration: float) -> None:
        self._log(f"Интерфейс снова отвечает, блокировка длилась {duration:.1f}с", "INFO")


_WATCHDOG: UiFreezeWatchdog | None = None


def install_ui_freeze_watchdog() -> UiFreezeWatchdog:
    """Ставит наблюдателя один раз на процесс."""
    global _WATCHDOG
    if _WATCHDOG is None:
        _WATCHDOG = UiFreezeWatchdog()
        _WATCHDOG.start()
    return _WATCHDOG


def shutdown_ui_freeze_watchdog() -> None:
    global _WATCHDOG
    watchdog = _WATCHDOG
    _WATCHDOG = None
    if watchdog is not None:
        watchdog.stop()


__all__ = [
    "FREEZE_REPEAT_SECONDS",
    "FREEZE_THRESHOLD_SECONDS",
    "HEARTBEAT_INTERVAL_MS",
    "UiFreezeWatchdog",
    "install_ui_freeze_watchdog",
    "shutdown_ui_freeze_watchdog",
]
