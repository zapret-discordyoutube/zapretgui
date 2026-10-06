"""Сколько времени пользователь ничего не делает мышью и клавиатурой.

Нужно задачам, которые занимают GUI-поток на десятки миллисекунд (сборка
скрытой страницы): в паузе такой рывок никто не заметит, а во время движения
мыши или клика он превращается в «программа подлагивает».

Источник — счётчик Windows `GetLastInputInfo`: он общий на весь сеанс и не
требует фильтра событий на всё приложение, через который иначе проходило бы
каждое событие Qt.
"""

from __future__ import annotations

import sys


def user_idle_ms() -> int | None:
    """Миллисекунды с последнего ввода. None — система не умеет ответить."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _LastInputInfo(ctypes.Structure):
            _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))

        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        kernel32.GetTickCount.restype = wintypes.DWORD
        # Оба счётчика 32-битные и переполняются раз в ~49 дней: разность
        # берём по модулю, чтобы переполнение не дало отрицательную паузу.
        return int((int(kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF)
    except Exception:
        return None


__all__ = ["user_idle_ms"]
