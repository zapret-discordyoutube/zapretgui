"""Приоритет окна над фоновыми потоками, пока окно программы на экране.

В Python одновременно выполняется только один поток: остальные ждут общий
замок интерпретатора (GIL). Когда GUI-потоку нужно выполнить питоновский код
(таймер, обработчик сигнала, отрисовку своего виджета), а фоновый поток в это
время что-то считает, GUI-поток просит замок и ждёт. Сколько он ждёт,
определяют две настройки.

**Интервал переключения** (`sys.setswitchinterval`) — главная. Ждущий поток
ждёт этот интервал и только потом просит работающий отдать замок. Windows не
умеет ждать меньше миллисекунды: интервал в 1 мс на деле длится ~2 мс, а
интервал *меньше* 1 мс CPython на Windows округляет до нуля — замок просят
сразу. Замер на win10, сборка страницы настройки профиля в GUI-потоке (без
фона 62–72 мс):

    интервал          рядом с 1 занятым потоком   рядом с 3
    5 мс (Python)            10023 мс              34763 мс
    1 мс                      2568 мс               9937 мс
    0,5 мс                     113 мс                254 мс

Плата: пока окну самому нужен процессор, фон работает примерно вдвое
медленнее, а ждущий замка поток не спит, а крутится. Поэтому короткий
интервал включён только пока окно видно; свернули или убрали в трей —
возвращается обычный: рывки там никто не увидит, а программа живёт в трее
большую часть времени.

**Точный системный таймер** (`timeBeginPeriod(1)`). Был нужен, пока интервал
равнялся 1 мс: без него то же ожидание длилось 16 мс (один тик Windows).
Теперь ожидание замка от таймера не зависит (тот же замер без точного
таймера: 92 и 160 мс). Таймер пока оставлен как был — включён, пока окно на
экране, — до отдельной проверки, не опираются ли на него анимации.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject


PRECISE_TIMER_PERIOD_MS = 1

# Меньше миллисекунды: на Windows ожидание замка тогда равно нулю (см. выше).
GUI_GIL_SWITCH_INTERVAL_SEC = 0.0005
# Значение Python по умолчанию: для окна, которого нет на экране.
BACKGROUND_GIL_SWITCH_INTERVAL_SEC = 0.005

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


def set_gui_gil_priority(enabled: bool) -> None:
    """Даёт GUI-потоку перехватывать замок Python у фоновых потоков без ожидания."""
    target = GUI_GIL_SWITCH_INTERVAL_SEC if enabled else BACKGROUND_GIL_SWITCH_INTERVAL_SEC
    try:
        if sys.getswitchinterval() != target:
            sys.setswitchinterval(target)
    except (AttributeError, ValueError):
        pass


def set_window_on_screen(shown: bool) -> None:
    """Включает обе настройки для видимого окна и выключает для скрытого."""
    set_gui_gil_priority(shown)
    set_precise_timer(shown)


class WindowPreciseTimer(QObject):
    """Держит приоритет окна над фоном включённым, только пока окно на экране."""

    def __init__(self, window, *, apply: Callable[[bool], object] = set_window_on_screen) -> None:
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
    "BACKGROUND_GIL_SWITCH_INTERVAL_SEC",
    "GUI_GIL_SWITCH_INTERVAL_SEC",
    "PRECISE_TIMER_PERIOD_MS",
    "WindowPreciseTimer",
    "install_window_precise_timer",
    "is_precise_timer_on",
    "set_gui_gil_priority",
    "set_precise_timer",
    "set_window_on_screen",
]
