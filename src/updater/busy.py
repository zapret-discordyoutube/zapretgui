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
# (SHQueryUserNotificationState): окно на весь экран, игра Direct3D на весь
# экран, режим презентации.
_QUNS_BUSY = 2
_QUNS_RUNNING_D3D_FULL_SCREEN = 3
_QUNS_PRESENTATION_MODE = 4
_FULLSCREEN_STATES = (_QUNS_BUSY, _QUNS_RUNNING_D3D_FULL_SCREEN, _QUNS_PRESENTATION_MODE)


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


def fullscreen_app_active() -> bool:
    """На экране игра, видео или презентация во весь экран."""
    return _notification_state() in _FULLSCREEN_STATES


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


__all__ = ["ACTIVITY_TRAY", "ACTIVITY_WINDOW", "BUSY_FULLSCREEN", "activity", "busy_reason", "fullscreen_app_active"]
