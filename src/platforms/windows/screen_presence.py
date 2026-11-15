"""Видит ли кто-нибудь экран: блокировка сеанса и выключенный дисплей.

Программа работает сутками, а окно при этом остаётся «видимым» для Qt и
ночью, когда дисплей погас, и на экране блокировки Windows. Анимации всё это
время рисовали кадры в пустоту. Здесь окно подписывается на два системных
уведомления Windows и останавливает общий такт кадров (ui.frame_clock), пока
картинку некому смотреть:

- WM_WTSSESSION_CHANGE — сеанс заблокирован/разблокирован, отключён/подключён;
- WM_POWERBROADCAST с GUID_CONSOLE_DISPLAY_STATE — дисплей выключен/включён.

На внешний вид это не влияет: кадры пропадают только тогда, когда их не видно.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from log.log import log


WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
SCREEN_PRESENCE_MESSAGES = frozenset({WM_POWERBROADCAST, WM_WTSSESSION_CHANGE})

PBT_POWERSETTINGCHANGE = 0x8013
DISPLAY_OFF = 0

WTS_CONSOLE_CONNECT = 0x1
WTS_CONSOLE_DISCONNECT = 0x2
WTS_REMOTE_CONNECT = 0x3
WTS_REMOTE_DISCONNECT = 0x4
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8

PAUSE_DISPLAY_OFF = "display_off"
PAUSE_SESSION_LOCKED = "session_locked"
PAUSE_SESSION_DISCONNECTED = "session_disconnected"

_NOTIFY_FOR_THIS_SESSION = 0
_DEVICE_NOTIFY_WINDOW_HANDLE = 0
_POWER_HANDLE_ATTR = "_zapret_display_state_notification"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _POWERBROADCAST_SETTING(ctypes.Structure):  # noqa: N801 - имя структуры Windows
    _fields_ = [
        ("PowerSetting", _GUID),
        ("DataLength", wintypes.DWORD),
        ("Data", wintypes.DWORD),
    ]


# GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}
_GUID_CONSOLE_DISPLAY_STATE = _GUID(
    0x6FE69556,
    0x704A,
    0x47A0,
    (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47),
)


def native_message_id(message, *, platform: str | None = None) -> int | None:
    """Номер сообщения Windows из указателя на MSG; не на Windows — None."""
    if (platform or sys.platform) != "win32":
        return None
    try:
        return int(wintypes.MSG.from_address(int(message)).message)
    except Exception:
        return None


def session_pause_change(session_event: int) -> tuple[str, bool] | None:
    """Что событие сеанса значит для анимаций: (причина паузы, включить ли её)."""
    if session_event == WTS_SESSION_LOCK:
        return PAUSE_SESSION_LOCKED, True
    if session_event == WTS_SESSION_UNLOCK:
        return PAUSE_SESSION_LOCKED, False
    if session_event in (WTS_CONSOLE_DISCONNECT, WTS_REMOTE_DISCONNECT):
        return PAUSE_SESSION_DISCONNECTED, True
    if session_event in (WTS_CONSOLE_CONNECT, WTS_REMOTE_CONNECT):
        return PAUSE_SESSION_DISCONNECTED, False
    return None


def display_pause_change(display_state: int) -> tuple[str, bool]:
    """Дисплей выключен — пауза; включён или притушен — кадры идут."""
    return PAUSE_DISPLAY_OFF, int(display_state) == DISPLAY_OFF


def _read_display_state(lparam: int) -> int | None:
    try:
        setting = _POWERBROADCAST_SETTING.from_address(int(lparam))
    except Exception:
        return None
    if bytes(setting.PowerSetting) != bytes(_GUID_CONSOLE_DISPLAY_STATE):
        return None
    if int(setting.DataLength) < ctypes.sizeof(wintypes.DWORD):
        return None
    return int(setting.Data)


def handle_native_screen_presence(message, *, clock=None) -> None:
    """Разбирает WM_WTSSESSION_CHANGE / WM_POWERBROADCAST и ставит такт на паузу.

    Сообщение не «съедается»: Qt и Windows обрабатывают его как обычно.
    """
    try:
        msg = wintypes.MSG.from_address(int(message))
        message_id = int(msg.message)
        wparam = int(msg.wParam)
        lparam = int(msg.lParam)
    except Exception:
        return

    change = None
    if message_id == WM_WTSSESSION_CHANGE:
        change = session_pause_change(wparam)
    elif message_id == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE:
        display_state = _read_display_state(lparam)
        if display_state is not None:
            change = display_pause_change(display_state)
    if change is None:
        return

    if clock is None:
        from ui.frame_clock import frame_clock

        clock = frame_clock()
    reason, paused = change
    clock.set_paused(reason, paused)


def register_screen_presence_notifications(window) -> bool:
    """Подписывает текущий HWND окна на уведомления о сеансе и дисплее."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = wintypes.HWND(int(window.winId()))
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
        wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
        session_ok = bool(wtsapi32.WTSRegisterSessionNotification(hwnd, _NOTIFY_FOR_THIS_SESSION))

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterPowerSettingNotification.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
        ]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
        user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL

        previous = getattr(window, _POWER_HANDLE_ATTR, None)
        if previous:
            # Прежняя подписка относилась к старому HWND.
            user32.UnregisterPowerSettingNotification(previous)
        handle = user32.RegisterPowerSettingNotification(
            hwnd,
            ctypes.byref(_GUID_CONSOLE_DISPLAY_STATE),
            _DEVICE_NOTIFY_WINDOW_HANDLE,
        )
        setattr(window, _POWER_HANDLE_ATTR, handle)
        return session_ok and bool(handle)
    except Exception as exc:
        log(f"Не удалось подписаться на состояние экрана и сеанса Windows: {exc}", "DEBUG")
        return False


__all__ = [
    "PAUSE_DISPLAY_OFF",
    "PAUSE_SESSION_DISCONNECTED",
    "PAUSE_SESSION_LOCKED",
    "SCREEN_PRESENCE_MESSAGES",
    "display_pause_change",
    "handle_native_screen_presence",
    "native_message_id",
    "register_screen_presence_notifications",
    "session_pause_change",
]
