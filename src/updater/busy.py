from __future__ import annotations

"""Занят ли человек делом, которое обновление оборвало бы.

Обновление закрывает программу и на несколько секунд останавливает Zapret.
Посреди игры на весь экран это обрыв связи, посреди проверки сети или
подбора стратегии — потерянный результат. Поэтому программа, которая
обновляется сама, сначала спрашивает здесь, удобно ли сейчас.

На обновление по кнопке это не влияет: человек сам решил, что ему удобно.

Модуль не импортирует Qt: его зовут и из потока слушателя выпусков.
"""

import sys

BUSY_FULLSCREEN = "fullscreen"

# Ответы Windows на вопрос «можно ли сейчас показывать уведомления»
# (SHQueryUserNotificationState).
_QUNS_BUSY = 2
_QUNS_RUNNING_D3D_FULL_SCREEN = 3
_QUNS_PRESENTATION_MODE = 4
# Игра Direct3D на весь экран и режим презентации — ответы однозначные.
_SURE_STATES = (_QUNS_RUNNING_D3D_FULL_SCREEN, _QUNS_PRESENTATION_MODE)

# Что стоит на переднем плане.
FOREGROUND_FULL = "f"     # чужое окно закрывает весь экран
FOREGROUND_PART = "p"     # окно занимает часть экрана
FOREGROUND_SHELL = "s"    # рабочий стол или панель задач
FOREGROUND_OWN = "o"      # окно самой программы
_SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def _notification_state() -> int:
    """Состояние экрана по мнению Windows; 0 — узнать не удалось."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return 0
        return int(state.value)
    except Exception:
        return 0


def _foreground() -> str:
    """Что на переднем плане: одна буква ``FOREGROUND_*`` либо пусто — не узнать."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        import os
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        window = user32.GetForegroundWindow()
        if not window:
            return FOREGROUND_SHELL
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(window, name, 64)
        if name.value in _SHELL_CLASSES:
            return FOREGROUND_SHELL
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == os.getpid():
            return FOREGROUND_OWN

        class _MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        rect = wintypes.RECT()
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        monitor = user32.MonitorFromWindow(window, 2)  # ближайший к окну экран
        if not user32.GetWindowRect(window, ctypes.byref(rect)) or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return ""
        screen = info.rcMonitor
        covers = (
            rect.left <= screen.left and rect.top <= screen.top
            and rect.right >= screen.right and rect.bottom >= screen.bottom
        )
        return FOREGROUND_FULL if covers else FOREGROUND_PART
    except Exception:
        return ""


def is_fullscreen(state: int, foreground: str) -> bool:
    """Занят ли экран игрой, видео или презентацией — по двум независимым признакам.

    Игра Direct3D на весь экран и режим презентации — ответы Windows
    однозначные. Ответ «экран занят» (2) расплывчат: его дают и невидимые
    окна поверх экрана, и чужие оболочки. Ему программа верит, только если
    окно на переднем плане и правда закрывает весь экран и это не рабочий
    стол и не она сама. Иначе обновление откладывалось бы у людей, которые
    ничем не заняты.
    """
    if state in _SURE_STATES:
        return True
    return state == _QUNS_BUSY and foreground == FOREGROUND_FULL


def screen_state() -> str:
    """Оба признака одной строкой, например ``2f`` или ``5p``: код ответа
    Windows и что на переднем плане. Уходит серверу общим счётом — по нему
    видно, насколько правилу «занят» можно верить."""
    state = _notification_state()
    return f"{state}{_foreground()}" if state else ""


def fullscreen_app_active() -> bool:
    """На экране игра, видео или презентация во весь экран."""
    state = _notification_state()
    if state in _SURE_STATES:
        return True
    return state == _QUNS_BUSY and _foreground() == FOREGROUND_FULL


def busy_reason() -> str:
    """Чем занят человек: название дела либо пустая строка — можно обновляться."""
    from core.runtime.long_tasks import active_long_tasks

    tasks = active_long_tasks()
    if tasks:
        return tasks[0]
    if fullscreen_app_active():
        return BUSY_FULLSCREEN
    return ""


ACTIVITY_WINDOW = "window"
ACTIVITY_TRAY = "tray"


def activity() -> str:
    """Чем занята программа: дело, которое нельзя обрывать, либо «окно открыто» / «в трее».

    Это слово уходит серверу вместе с вопросом о новой версии: на сайте
    показан общий счёт, чем заняты программы. Пустая строка — неизвестно.
    """
    from core.runtime.presence import window_shown

    reason = busy_reason()
    if reason:
        return reason
    shown = window_shown()
    if shown is None:
        return ""
    return ACTIVITY_WINDOW if shown else ACTIVITY_TRAY


__all__ = [
    "ACTIVITY_TRAY",
    "ACTIVITY_WINDOW",
    "BUSY_FULLSCREEN",
    "activity",
    "busy_reason",
    "fullscreen_app_active",
    "is_fullscreen",
    "screen_state",
]
