"""Приоритет окна над фоновыми потоками, пока окно программы на экране.

В Python одновременно выполняется только один поток: остальные ждут общий
замок интерпретатора (GIL). Когда GUI-потоку нужно выполнить питоновский код
(таймер, обработчик сигнала, отрисовку своего виджета), а фоновый поток в это
время что-то считает, GUI-поток просит замок и ждёт.

Сколько он ждёт, задаёт интервал переключения (`sys.setswitchinterval`):
ждущий поток ждёт этот интервал и только потом просит работающий отдать
замок. Windows не умеет ждать меньше миллисекунды: интервал в 1 мс на деле
длится ~2 мс (а при обычном шаге системных часов — все 16 мс), зато интервал
*меньше* 1 мс CPython на Windows округляет до нуля — замок просят сразу.
Замер на win10, сборка страницы настройки профиля в GUI-потоке (без фона
62–72 мс):

    интервал          рядом с 1 занятым потоком   рядом с 3
    5 мс (Python)            10023 мс              34763 мс
    1 мс                      2568 мс               9937 мс
    0,5 мс                      92 мс                160 мс

Плата: пока окну самому нужен процессор, фон работает примерно вдвое
медленнее, а ждущий замка поток не спит, а крутится. Поэтому короткий
интервал включён только пока окно видно; свернули или убрали в трей —
возвращается обычный: рывки там никто не увидит, а программа живёт в трее
большую часть времени.

Раньше здесь же включался точный системный таймер Windows
(`timeBeginPeriod(1)`): при интервале в 1 мс без него ожидание длилось 16 мс.
С интервалом меньше миллисекунды ожидание от шага часов не зависит, а
анимациям он не нужен — Qt ведёт свои точные таймеры сам (замер на win10:
такт 33 мс даёт кадры через 33,0 мс и с ним, и без него), поэтому он удалён.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject


# Меньше миллисекунды: на Windows ожидание замка тогда равно нулю (см. выше).
GUI_GIL_SWITCH_INTERVAL_SEC = 0.0005
# Значение Python по умолчанию: для окна, которого нет на экране.
BACKGROUND_GIL_SWITCH_INTERVAL_SEC = 0.005

_WATCHED_EVENTS = (QEvent.Type.Hide, QEvent.Type.Show, QEvent.Type.WindowStateChange)


def set_gui_gil_priority(enabled: bool) -> None:
    """Даёт GUI-потоку перехватывать замок Python у фоновых потоков без ожидания."""
    target = GUI_GIL_SWITCH_INTERVAL_SEC if enabled else BACKGROUND_GIL_SWITCH_INTERVAL_SEC
    try:
        if sys.getswitchinterval() != target:
            sys.setswitchinterval(target)
    except (AttributeError, ValueError):
        pass


class WindowGilPriority(QObject):
    """Держит приоритет окна над фоном включённым, только пока окно на экране."""

    def __init__(self, window, *, apply: Callable[[bool], object] = set_gui_gil_priority) -> None:
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


def install_window_gil_priority(window) -> WindowGilPriority:
    return WindowGilPriority(window)


__all__ = [
    "BACKGROUND_GIL_SWITCH_INTERVAL_SEC",
    "GUI_GIL_SWITCH_INTERVAL_SEC",
    "WindowGilPriority",
    "install_window_gil_priority",
    "set_gui_gil_priority",
]
