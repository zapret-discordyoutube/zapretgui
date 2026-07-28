"""Снимки стеков всех потоков для разбора зависаний.

`crash_handler` покрывает падения, но не зависания: процесс жив, окно не
отвечает, а в логе — пустой промежуток. Здесь лежат два инструмента, которыми
`ui.ui_freeze_watchdog` превращает такой эпизод в улику.

Почему уровня два:

* `format_thread_dump()` — питоновский снимок: имена потоков и Python-стеки.
  Читаемый, но выполняется только пока GIL свободен, то есть когда GUI-поток
  занят долгой работой, а не мёртв.
* `FaulthandlerHangTimer` — сторожевой таймер в нативном потоке. Он не берёт
  GIL, поэтому пишет стеки и при настоящем дедлоке: правка QWidget из чужого
  потока на Windows уходит в `SendMessage` оконному потоку, который не может
  ответить, пока вызывающий держит GIL, — в таком эпизоде питоновский код в
  процессе больше не исполняется вообще.

Модуль намеренно не знает про Qt: Qt-обвязка живёт в `ui/ui_freeze_watchdog.py`.
"""

from __future__ import annotations

import datetime
import os
import sys
import threading
import traceback
from pathlib import Path

DISABLE_ENV_VAR = "ZAPRET_DISABLE_HANG_DUMPS"
FAULTHANDLER_DUMP_NAME = "hangs_faulthandler.log"


def is_thread_dump_disabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return str(source.get(DISABLE_ENV_VAR, "")).strip() == "1"


def format_thread_dump(
    *,
    stalled_seconds: float,
    gui_thread_ident: int | None = None,
    frames: dict[int, object] | None = None,
    threads: list[threading.Thread] | None = None,
    now_text: str | None = None,
) -> str:
    """Читаемый отчёт: имена потоков + Python-стеки на момент вызова."""
    timestamp = now_text or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    current_frames = sys._current_frames() if frames is None else frames
    thread_list = list(threading.enumerate()) if threads is None else list(threads)
    names = {int(thread.ident): thread for thread in thread_list if thread.ident is not None}

    lines = [
        "=" * 80,
        f"🧊 UI FREEZE REPORT - {timestamp}",
        "=" * 80,
        "",
        f"Интерфейс не отвечает: {stalled_seconds:.1f}с",
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
        except Exception as exc:  # pragma: no cover - защита от гонки со снятием потока
            lines.append(f"<не удалось получить стек: {exc}>")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def dump_reports_dir() -> Path:
    from config.runtime_layout import APPLICATION_PATHS

    return Path(APPLICATION_PATHS.crash_logs_dir)


def save_thread_dump(
    report: str,
    *,
    folder: Path | str | None = None,
    now_text: str | None = None,
) -> str:
    """Кладёт отчёт рядом с крэш-логами. Возвращает путь либо пустую строку."""
    try:
        target = Path(folder) if folder is not None else dump_reports_dir()
        target.mkdir(parents=True, exist_ok=True)
        timestamp = now_text or datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = target / f"freeze_{timestamp}.log"
        path.write_text(report, encoding="utf-8")
        return str(path)
    except Exception:
        return ""


class FaulthandlerHangTimer:
    """Сторожевой таймер без GIL: единственный уровень, переживающий дедлок.

    Каждое `arm()` перевзводит таймер, поэтому пока GUI-поток жив и бьётся,
    дампов не будет. Молчание дольше таймаута — файл со стеками всех потоков.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._file = None
        self._armed = False

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_file(self):
        if self._file is not None:
            return self._file
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a", encoding="utf-8")
        handle.write(f"\n{'=' * 60}\n")
        handle.write(f"Session started: {datetime.datetime.now()}\n")
        handle.write(f"{'=' * 60}\n\n")
        handle.flush()
        self._file = handle
        return handle

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


__all__ = [
    "DISABLE_ENV_VAR",
    "FAULTHANDLER_DUMP_NAME",
    "FaulthandlerHangTimer",
    "dump_reports_dir",
    "format_thread_dump",
    "is_thread_dump_disabled",
    "save_thread_dump",
]
