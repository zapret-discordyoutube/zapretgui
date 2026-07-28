"""Наблюдение за отзывчивостью GUI-потока и дамп стеков при зависании.

`crash_handler` покрывает падения, но не зависания: при дедлоке процесс жив,
окно не отвечает, а в логах пусто — разбирать нечего. Watchdog делает такой
эпизод наблюдаемым.

Уровней два, потому что зависания бывают разной природы:

* `faulthandler.dump_traceback_later()` — сторожевой таймер в нативном потоке.
  Он не берёт GIL, поэтому пишет стеки даже когда GUI-поток заблокирован
  фоновым потоком, удерживающим GIL: правка QWidget из чужого потока на Windows
  уходит в `SendMessage` оконному потоку, который не может ответить, и оба
  потока стоят навсегда. Питоновский наблюдатель в таком эпизоде не выполнится.
* Питоновский наблюдатель — читаемый отчёт с именами потоков и Python-стеками
  плюс запись в основной лог. Работает, когда GIL свободен, то есть GUI-поток
  занят долгой операцией, а не мёртв.

GUI-поток раз в `beat_seconds` ставит отметку. Отсутствие отметки дольше
`threshold_seconds` — эпизод зависания.
"""

from __future__ import annotations

import datetime
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_THRESHOLD_SECONDS = 6.0
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_BEAT_SECONDS = 2.0
DEFAULT_REPEAT_SECONDS = 30.0
DEFAULT_MAX_DUMPS_PER_EPISODE = 3

DISABLE_ENV_VAR = "ZAPRET_DISABLE_HANG_WATCHDOG"
LOG_LEVEL = "⚠️ HANG"


@dataclass(frozen=True, slots=True)
class HangDecision:
    """Что наблюдатель решил сделать по одному замеру."""

    should_dump: bool = False
    recovered: bool = False
    episode: int = 0
    dump_index: int = 0
    stalled_seconds: float = 0.0


class HangDetector:
    """Чистое состояние наблюдателя: только отметки времени, без потоков и Qt.

    Отделено от потока и файловой записи, чтобы поведение (порог, дедупликация
    эпизода, повторные дампы, восстановление) проверялось без ожиданий в тестах.
    """

    def __init__(
        self,
        *,
        threshold_seconds: float = DEFAULT_THRESHOLD_SECONDS,
        repeat_seconds: float = DEFAULT_REPEAT_SECONDS,
        max_dumps_per_episode: int = DEFAULT_MAX_DUMPS_PER_EPISODE,
    ) -> None:
        self._threshold_seconds = max(float(threshold_seconds), 0.0)
        self._repeat_seconds = max(float(repeat_seconds), 0.0)
        self._max_dumps_per_episode = max(int(max_dumps_per_episode), 1)
        self._last_beat: float | None = None
        self._episode = 0
        self._dumps_in_episode = 0
        self._last_dump_at = 0.0
        self._peak_stalled = 0.0
        self._in_episode = False

    @property
    def threshold_seconds(self) -> float:
        return self._threshold_seconds

    @property
    def episode(self) -> int:
        return self._episode

    def beat(self, now: float) -> None:
        self._last_beat = float(now)

    def evaluate(self, now: float) -> HangDecision:
        # До первой отметки судить не о чем: наблюдатель мог стартовать раньше
        # GUI-таймера, и любой замер до этого дал бы ложный эпизод.
        if self._last_beat is None:
            return HangDecision()

        stalled = max(float(now) - self._last_beat, 0.0)
        if stalled < self._threshold_seconds:
            if not self._in_episode:
                return HangDecision()
            decision = HangDecision(
                recovered=True,
                episode=self._episode,
                stalled_seconds=self._peak_stalled,
            )
            self._reset_episode()
            return decision

        self._peak_stalled = max(self._peak_stalled, stalled)
        if not self._in_episode:
            self._in_episode = True
            self._episode += 1
            self._dumps_in_episode = 1
            self._last_dump_at = float(now)
            return HangDecision(
                should_dump=True,
                episode=self._episode,
                dump_index=1,
                stalled_seconds=stalled,
            )

        quiet = HangDecision(episode=self._episode, stalled_seconds=stalled)
        if self._dumps_in_episode >= self._max_dumps_per_episode:
            return quiet
        if float(now) - self._last_dump_at < self._repeat_seconds:
            return quiet

        self._dumps_in_episode += 1
        self._last_dump_at = float(now)
        return HangDecision(
            should_dump=True,
            episode=self._episode,
            dump_index=self._dumps_in_episode,
            stalled_seconds=stalled,
        )

    def _reset_episode(self) -> None:
        self._in_episode = False
        self._dumps_in_episode = 0
        self._last_dump_at = 0.0
        self._peak_stalled = 0.0


def format_thread_dump(
    *,
    stalled_seconds: float,
    episode: int,
    dump_index: int,
    gui_thread_ident: int | None = None,
    frames: dict[int, object] | None = None,
    threads: list[threading.Thread] | None = None,
    now_text: str | None = None,
) -> str:
    """Собирает читаемый отчёт: имена потоков + Python-стеки на момент замера."""
    timestamp = now_text or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    current_frames = sys._current_frames() if frames is None else frames
    thread_list = list(threading.enumerate()) if threads is None else list(threads)
    names = {int(thread.ident): thread for thread in thread_list if thread.ident is not None}

    lines = [
        "=" * 80,
        f"🧊 GUI HANG REPORT - {timestamp}",
        "=" * 80,
        "",
        f"Эпизод: {episode} (дамп #{dump_index})",
        f"GUI-поток не отвечает: {stalled_seconds:.1f}с",
        f"GUI thread id: {gui_thread_ident if gui_thread_ident is not None else 'неизвестен'}",
        f"Всего потоков: {len(thread_list)}",
        "",
    ]

    for ident, frame in sorted(current_frames.items(), key=lambda item: item[0]):
        thread = names.get(int(ident))
        name = thread.name if thread is not None else "<unknown>"
        marks = []
        if thread is not None and thread.daemon:
            marks.append("daemon")
        if gui_thread_ident is not None and int(ident) == int(gui_thread_ident):
            marks.append("GUI")
        suffix = f" [{', '.join(marks)}]" if marks else ""
        lines.append("─" * 40)
        lines.append(f"Thread {ident}: {name}{suffix}")
        lines.append("─" * 40)
        try:
            lines.extend(line.rstrip() for line in traceback.format_stack(frame))
        except Exception as exc:  # pragma: no cover - защита от гонок со снятием потока
            lines.append(f"<не удалось получить стек: {exc}>")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


class FaulthandlerHangTimer:
    """Сторожевой таймер без GIL: единственный уровень, переживающий дедлок."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._file = None
        self._armed = False

    def _ensure_file(self):
        if self._file is not None:
            return self._file
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        self._file.write(f"\n{'=' * 60}\n")
        self._file.write(f"Session started: {datetime.datetime.now()}\n")
        self._file.write(f"{'=' * 60}\n\n")
        self._file.flush()
        return self._file

    def arm(self, timeout_seconds: float) -> None:
        import faulthandler

        handle = self._ensure_file()
        faulthandler.dump_traceback_later(
            max(float(timeout_seconds), 0.1),
            repeat=False,
            file=handle,
            exit=False,
        )
        self._armed = True

    def disarm(self) -> None:
        import faulthandler

        if not self._armed:
            return
        faulthandler.cancel_dump_traceback_later()
        self._armed = False

    def close(self) -> None:
        self.disarm()
        handle = self._file
        self._file = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _default_dump_dir() -> Path:
    from config.runtime_layout import APPLICATION_PATHS

    return Path(APPLICATION_PATHS.crash_logs_dir)


def _default_log(message: str, level: str) -> None:
    from log.log import log

    log(message, level)


class GuiHangWatchdog:
    """Демонический наблюдатель: замечает молчание GUI-потока и пишет отчёт."""

    def __init__(
        self,
        *,
        threshold_seconds: float = DEFAULT_THRESHOLD_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        repeat_seconds: float = DEFAULT_REPEAT_SECONDS,
        max_dumps_per_episode: int = DEFAULT_MAX_DUMPS_PER_EPISODE,
        dump_dir: Path | str | None = None,
        clock: Callable[[], float] = time.monotonic,
        log_fn: Callable[[str, str], None] | None = None,
        native_timer=None,
    ) -> None:
        self._detector = HangDetector(
            threshold_seconds=threshold_seconds,
            repeat_seconds=repeat_seconds,
            max_dumps_per_episode=max_dumps_per_episode,
        )
        self._poll_seconds = max(float(poll_seconds), 0.05)
        self._clock = clock
        self._log = log_fn if log_fn is not None else _default_log
        self._dump_dir = Path(dump_dir) if dump_dir is not None else None
        self._native_timer = native_timer
        self._native_timer_owned = native_timer is None
        self._gui_thread_ident: int | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._qt_timer = None

    @property
    def detector(self) -> HangDetector:
        return self._detector

    @property
    def gui_thread_ident(self) -> int | None:
        return self._gui_thread_ident

    def resolve_dump_dir(self) -> Path:
        if self._dump_dir is None:
            self._dump_dir = _default_dump_dir()
        return self._dump_dir

    def beat(self) -> None:
        """Вызывается из GUI-потока: подтверждает, что цикл событий жив."""
        self._gui_thread_ident = threading.get_ident()
        with self._lock:
            self._detector.beat(self._clock())
        self._arm_native_timer()

    def start(self) -> None:
        if self._thread is not None:
            return
        self.beat()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="GuiHangWatchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(float(timeout), 0.0))
        timer = self._qt_timer
        self._qt_timer = None
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        if self._native_timer is not None and self._native_timer_owned:
            try:
                self._native_timer.close()
            except Exception:
                pass
            self._native_timer = None

    def attach_qt_timer(self, timer) -> None:
        self._qt_timer = timer

    def poll_once(self) -> HangDecision:
        """Один замер: вынесен из цикла, чтобы тесты не ждали реального времени."""
        with self._lock:
            decision = self._detector.evaluate(self._clock())
        if decision.should_dump:
            self._handle_dump(decision)
        elif decision.recovered:
            self._handle_recovery(decision)
        return decision

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_seconds):
            try:
                self.poll_once()
            except Exception as exc:  # pragma: no cover - наблюдатель не имеет права падать
                self._safe_log(f"Сбой наблюдателя зависаний: {exc}", "DEBUG")

    def _handle_dump(self, decision: HangDecision) -> None:
        report = format_thread_dump(
            stalled_seconds=decision.stalled_seconds,
            episode=decision.episode,
            dump_index=decision.dump_index,
            gui_thread_ident=self._gui_thread_ident,
        )
        path = self._save_report(report, decision)
        location = f" Отчёт: {path}" if path else ""
        self._safe_log(
            f"GUI-поток не отвечает {decision.stalled_seconds:.1f}с "
            f"(эпизод {decision.episode}, дамп #{decision.dump_index}).{location}",
            LOG_LEVEL,
        )

    def _handle_recovery(self, decision: HangDecision) -> None:
        self._safe_log(
            f"GUI-поток снова отвечает; эпизод {decision.episode} длился "
            f"не менее {decision.stalled_seconds:.1f}с",
            LOG_LEVEL,
        )

    def _save_report(self, report: str, decision: HangDecision) -> str:
        try:
            folder = self.resolve_dump_dir()
            folder.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = folder / f"hang_{timestamp}_e{decision.episode}_{decision.dump_index}.log"
            path.write_text(report, encoding="utf-8")
            return str(path)
        except Exception as exc:
            self._safe_log(f"Не удалось сохранить отчёт о зависании: {exc}", "DEBUG")
            return ""

    def _arm_native_timer(self) -> None:
        timer = self._native_timer
        if timer is None and self._native_timer_owned:
            try:
                timer = FaulthandlerHangTimer(self.resolve_dump_dir() / "hangs_faulthandler.log")
            except Exception as exc:
                self._safe_log(f"Сторожевой таймер faulthandler недоступен: {exc}", "DEBUG")
                self._native_timer_owned = False
                return
            self._native_timer = timer
        if timer is None:
            return
        try:
            timer.arm(self._detector.threshold_seconds)
        except Exception as exc:
            self._safe_log(f"Сторожевой таймер faulthandler не взведён: {exc}", "DEBUG")
            self._native_timer = None
            self._native_timer_owned = False

    def _safe_log(self, message: str, level: str) -> None:
        try:
            self._log(message, level)
        except Exception:
            pass


def is_hang_watchdog_disabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(DISABLE_ENV_VAR, "")).strip() == "1"


def install_gui_hang_watchdog(
    app=None,
    *,
    threshold_seconds: float = DEFAULT_THRESHOLD_SECONDS,
    beat_seconds: float = DEFAULT_BEAT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    repeat_seconds: float = DEFAULT_REPEAT_SECONDS,
    max_dumps_per_episode: int = DEFAULT_MAX_DUMPS_PER_EPISODE,
) -> GuiHangWatchdog | None:
    """Ставит биение в GUI-потоке и запускает наблюдателя. Вызывать из GUI-потока."""
    if is_hang_watchdog_disabled():
        return None

    from PyQt6.QtCore import QTimer

    watchdog = GuiHangWatchdog(
        threshold_seconds=threshold_seconds,
        poll_seconds=poll_seconds,
        repeat_seconds=repeat_seconds,
        max_dumps_per_episode=max_dumps_per_episode,
    )

    timer = QTimer(app)
    timer.setInterval(max(int(float(beat_seconds) * 1000), 100))
    timer.timeout.connect(watchdog.beat)
    timer.start()
    watchdog.attach_qt_timer(timer)
    watchdog.start()

    if app is not None:
        try:
            app.aboutToQuit.connect(watchdog.stop)
        except Exception:
            pass
    return watchdog


__all__ = [
    "DEFAULT_BEAT_SECONDS",
    "DEFAULT_MAX_DUMPS_PER_EPISODE",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_REPEAT_SECONDS",
    "DEFAULT_THRESHOLD_SECONDS",
    "DISABLE_ENV_VAR",
    "FaulthandlerHangTimer",
    "GuiHangWatchdog",
    "HangDecision",
    "HangDetector",
    "format_thread_dump",
    "install_gui_hang_watchdog",
    "is_hang_watchdog_disabled",
]
