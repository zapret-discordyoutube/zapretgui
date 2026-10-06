"""Точный системный таймер Windows, пока окно программы на экране.

Зачем это интерфейсу. В Python одновременно выполняется только один поток:
остальные ждут общий замок интерпретатора (GIL). Когда GUI-потоку нужно
выполнить питоновский код (таймер, обработчик сигнала, отрисовку своего
виджета), а фоновый поток в это время что-то считает, GUI-поток просит замок
и ждёт. Ждёт он по системному таймеру, а тот по умолчанию тикает раз в
15,6 мс — и `sys.setswitchinterval(0.001)` этого не меняет: меньше тика
Windows не ждёт.

Замер на win10 (фоновый поток занят счётом, GUI-поток возвращается из
короткого вызова C-кода):

    обычный таймер       16,0 мс на каждый возврат в Python
    таймер 1 мс           2,0 мс

Кадр, в котором Python вызывается три раза, с обычным таймером задерживается
на ~48 мс — ровно такие рывки видны в первые секунды после запуска, когда
работают сразу несколько фоновых задач.

Точный таймер немного увеличивает расход энергии, поэтому включён только пока
окно видно. Свернули или убрали в трей — возвращается обычный: рывки там
никто не увидит, а программа живёт в трее большую часть времени.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject


PRECISE_TIMER_PERIOD_MS = 1

_WATCHED_EVENTS = (QEvent.Type.Hide, QEvent.Type.Show, QEvent.Type.WindowStateChange)

_precise_timer_on = False


def _winmm_call(name: str) -> bool:
    try:
        import ctypes

        # 0 (TIMERR_NOERROR) — запрос принят.
        return int(getattr(ctypes.windll.winmm, name)(PRECISE_TIMER_PERIOD_MS)) == 0
    except Exception:
        return False


def set_precise_timer(
    enabled: bool,
    *,
    begin: Callable[[], bool] | None = None,
    end: Callable[[], bool] | None = None,
) -> bool:
    """Включает или выключает точный таймер. Возвращает итоговое состояние.

    Повторный вызов с тем же значением ничего не делает: у Windows каждому
    timeBeginPeriod должен соответствовать ровно один timeEndPeriod.
    """
    global _precise_timer_on
    wanted = bool(enabled)
    if wanted == _precise_timer_on:
        return _precise_timer_on
    if begin is None and end is None and sys.platform != "win32":
        return _precise_timer_on
    if wanted:
        if (begin or (lambda: _winmm_call("timeBeginPeriod")))():
            _precise_timer_on = True
    else:
        (end or (lambda: _winmm_call("timeEndPeriod")))()
        _precise_timer_on = False
    return _precise_timer_on


def is_precise_timer_on() -> bool:
    return _precise_timer_on


class WindowPreciseTimer(QObject):
    """Держит точный таймер включённым, только пока окно на экране."""

    def __init__(self, window, *, apply: Callable[[bool], object] = set_precise_timer) -> None:
        super().__init__(window)
        self._window = window
        self._apply = apply
        window.installEventFilter(self)
        self.sync()

    def sync(self) -> None:
        window = self._window
        shown = bool(window.isVisible()) and not bool(window.isMinimized())
        try:
            self._apply(shown)
        except Exception:
            pass

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if obj is self._window and event.type() in _WATCHED_EVENTS:
            self.sync()
        return False


def install_window_precise_timer(window) -> WindowPreciseTimer:
    return WindowPreciseTimer(window)


__all__ = [
    "PRECISE_TIMER_PERIOD_MS",
    "WindowPreciseTimer",
    "install_window_precise_timer",
    "is_precise_timer_on",
    "set_precise_timer",
]
