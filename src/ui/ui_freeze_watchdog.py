"""Наблюдатель за отзывчивостью интерфейса.

Заблокированный GUI-поток не оставляет следов: приложение просто перестаёт
рисовать, Windows показывает «программа не отвечает», а в логе — пустой
промежуток. Чтобы такие случаи диагностировались по обычному логу, а не
подключением профилировщика к чужому процессу, здесь измеряется задержка
event loop и при блокировке пишется стек GUI-потока.

Механика: таймер в GUI-потоке обновляет отметку времени, фоновый поток
сравнивает её с текущим временем. Разрыв больше порога — заморозка.

Наблюдение трёхслойное, потому что зависания бывают разной тяжести:

* короткая блокировка — строка в логе со стеком GUI-потока;
* долгая (`THREAD_DUMP_MIN_SECONDS`) — файл со стеками всех потоков рядом с
  крэш-логами: виновник обычно виден именно в чужом потоке;
* дедлок — сторожевой таймер `faulthandler` в нативном потоке. Он не берёт GIL
  и потому пишет стеки даже там, где этот наблюдатель уже не выполнится:
  правка QWidget из фонового потока на Windows уходит в `SendMessage` оконному
  потоку, который не может ответить, пока вызывающий держит GIL.
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

HEARTBEAT_INTERVAL_MS = 500
FREEZE_THRESHOLD_SECONDS = 2.0
# Пока интерфейс стоит, стек снимается повторно: длинная блокировка обычно
# проходит несколько стадий, и первая из них не самая интересная.
FREEZE_REPEAT_SECONDS = 10.0
# Отдельный порог для файлов: короткие подвисания случаются штатно, и дамп
# всех потоков на каждое из них засорил бы папку крэш-логов.
THREAD_DUMP_MIN_SECONDS = 5.0
# Таймаут дедлок-уровня. Заметно больше порога заморозки: сюда должны попадать
# только эпизоды, из которых интерфейс сам не вышел.
HARD_FREEZE_DUMP_SECONDS = 8.0


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
        thread_dump_min_seconds: float = THREAD_DUMP_MIN_SECONDS,
        hard_freeze_dump_seconds: float = HARD_FREEZE_DUMP_SECONDS,
        jitter_mode: bool = False,
        log_fn=None,
        stack_fn=None,
        dump_dir=None,
        native_timer=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        # Нижние границы малы ради jitter-режима: рывки интерфейса живут в
        # десятках миллисекунд, и наблюдателю с шагом 500 мс они не видны.
        self._heartbeat_interval_ms = max(5, int(heartbeat_interval_ms))
        self._freeze_threshold = max(0.02, float(freeze_threshold_seconds))
        self._repeat_seconds = max(0.02, float(repeat_seconds))
        self._thread_dump_min_seconds = max(0.0, float(thread_dump_min_seconds))
        self._hard_freeze_dump_seconds = max(0.2, float(hard_freeze_dump_seconds))
        self._jitter_mode = bool(jitter_mode)
        self._log_fn = log_fn
        self._stack_fn = stack_fn or _format_gui_stack
        self._dump_dir = dump_dir
        self._native_timer = native_timer
        self._native_timer_owned = native_timer is None
        self._native_timer_failed = False
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
        # Взведённый faulthandler пережил бы наблюдателя и дампил уже штатно
        # завершающийся процесс.
        native_timer = self._native_timer
        if native_timer is not None and self._native_timer_owned:
            self._native_timer = None
            try:
                native_timer.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Измерение
    # ------------------------------------------------------------------

    def beat(self) -> None:
        """Отметка живого event loop. Вызывается только в GUI-потоке."""
        with self._lock:
            self._last_beat = time.monotonic()
        self._arm_native_timer()

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

    def _is_jitter_mode(self) -> bool:
        """Режим охоты за рывками включается явно, а не угадывается по порогу."""
        return self._jitter_mode

    @staticmethod
    def _format_stall(stall: float) -> str:
        return f"{stall * 1000:.0f}мс" if stall < 1.0 else f"{stall:.1f}с"

    def _report_freeze_started(self, stall: float) -> None:
        headline = (
            f"Рывок интерфейса: кадр задержан на {self._format_stall(stall)}"
            if self._is_jitter_mode()
            else f"Интерфейс не отвечает {stall:.1f}с"
        )
        self._log(
            f"{headline}. Стек GUI-потока:\n{self._stack_fn()}"
            f"{self._save_full_dump(stall)}",
            "⚠ WARNING",
        )

    def _report_freeze_continues(self, stall: float) -> None:
        headline = (
            f"Интерфейс всё ещё стоит ({self._format_stall(stall)})"
            if self._is_jitter_mode()
            else f"Интерфейс всё ещё не отвечает ({stall:.1f}с)"
        )
        self._log(
            f"{headline}. Стек GUI-потока:\n{self._stack_fn()}"
            f"{self._save_full_dump(stall)}",
            "⚠ WARNING",
        )

    def _report_freeze_ended(self, duration: float) -> None:
        if self._is_jitter_mode():
            self._log(f"Интерфейс снова отвечает, рывок длился {self._format_stall(duration)}", "INFO")
            return
        self._log(f"Интерфейс снова отвечает, блокировка длилась {duration:.1f}с", "INFO")

    # ------------------------------------------------------------------
    # Дампы всех потоков
    # ------------------------------------------------------------------

    def _save_full_dump(self, stall: float) -> str:
        """Стеки всех потоков в файл: интерфейс обычно блокирует чужой поток.

        Возвращает готовый хвост для лог-сообщения, потому что путь к отчёту
        нужен там же, где сообщение о заморозке.
        """
        if stall < self._thread_dump_min_seconds:
            return ""
        try:
            from log.thread_dump import (
                format_thread_dump,
                is_thread_dump_disabled,
                save_thread_dump,
            )
            from ui.ui_thread_guard import gui_thread_id

            if is_thread_dump_disabled():
                return ""
            report = format_thread_dump(
                stalled_seconds=stall,
                gui_thread_ident=gui_thread_id(),
            )
            path = save_thread_dump(report, folder=self._dump_dir)
        except Exception:
            return ""
        return f"\nСтеки всех потоков: {path}" if path else ""

    def _arm_native_timer(self) -> None:
        """Перевзводит сторожевой таймер: живой event loop — значит дампа нет.

        Этот уровень нужен ровно там, где два верхних не работают: при дедлоке
        фоновый поток держит GIL, и питоновский наблюдатель не выполнится.
        """
        if self._native_timer_failed:
            return
        timer = self._native_timer
        if timer is None:
            if not self._native_timer_owned:
                return
            try:
                from log.thread_dump import (
                    FAULTHANDLER_DUMP_NAME,
                    FaulthandlerHangTimer,
                    dump_reports_dir,
                    is_thread_dump_disabled,
                )

                if is_thread_dump_disabled():
                    self._native_timer_failed = True
                    return
                folder = self._dump_dir if self._dump_dir is not None else dump_reports_dir()
                timer = FaulthandlerHangTimer(Path(folder) / FAULTHANDLER_DUMP_NAME)
            except Exception:
                self._native_timer_failed = True
                return
            self._native_timer = timer
        try:
            timer.arm(self._hard_freeze_dump_seconds)
        except Exception:
            # Без файла или без faulthandler остаются два верхних уровня.
            self._native_timer = None
            self._native_timer_failed = True


_WATCHDOG: UiFreezeWatchdog | None = None

# Диагностический режим для рывков: `ZAPRET_UI_JITTER_MS=60` заставляет
# наблюдателя ловить не «программа не отвечает», а короткие задержки кадра,
# из-за которых интерфейс дёргается. Порог задаётся в миллисекундах.
UI_JITTER_ENV = "ZAPRET_UI_JITTER_MS"
UI_JITTER_MIN_MS = 20


def jitter_threshold_ms() -> int:
    """Порог jitter-режима из окружения. 0 — режим выключен."""
    import os

    raw = str(os.environ.get(UI_JITTER_ENV, "") or "").strip()
    if not raw:
        return 0
    try:
        value = int(float(raw))
    except ValueError:
        return 0
    if value < UI_JITTER_MIN_MS:
        return 0
    return value


def build_watchdog_settings(jitter_ms: int) -> dict[str, float | int]:
    """Параметры наблюдателя: штатные или заточенные под ловлю рывков."""
    if jitter_ms <= 0:
        return {}
    threshold_seconds = jitter_ms / 1000.0
    return {
        "jitter_mode": True,
        # Бить чаще порога, иначе рывок укладывается между ударами.
        "heartbeat_interval_ms": max(5, jitter_ms // 3),
        "freeze_threshold_seconds": threshold_seconds,
        "repeat_seconds": max(0.2, threshold_seconds * 4),
        # thread_dump_min_seconds намеренно оставлен штатным: файлы со стеками
        # всех потоков нужны только для настоящих заморозок, иначе каждый рывок
        # писал бы отчёт в папку крэш-логов.
    }


def install_ui_freeze_watchdog() -> UiFreezeWatchdog:
    """Ставит наблюдателя один раз на процесс."""
    global _WATCHDOG
    if _WATCHDOG is None:
        jitter_ms = jitter_threshold_ms()
        _WATCHDOG = UiFreezeWatchdog(**build_watchdog_settings(jitter_ms))
        _WATCHDOG.start()
        if jitter_ms:
            try:
                from log.log import log

                log(
                    f"UI jitter watchdog: логируются задержки кадра дольше {jitter_ms}мс "
                    f"({UI_JITTER_ENV})",
                    "INFO",
                )
            except Exception:
                pass
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
    "HARD_FREEZE_DUMP_SECONDS",
    "HEARTBEAT_INTERVAL_MS",
    "THREAD_DUMP_MIN_SECONDS",
    "UI_JITTER_ENV",
    "UI_JITTER_MIN_MS",
    "UiFreezeWatchdog",
    "build_watchdog_settings",
    "install_ui_freeze_watchdog",
    "jitter_threshold_ms",
    "shutdown_ui_freeze_watchdog",
]
