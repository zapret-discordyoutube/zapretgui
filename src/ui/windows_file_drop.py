"""Приём файлов из Проводника Windows окном с правами администратора."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from log.log import log


WM_COPYGLOBALDATA = 0x0049
WM_COPYDATA = 0x004A
WM_DROPFILES = 0x0233
MSGFLT_ALLOW = 1
DROP_FILE_COUNT_INDEX = 0xFFFFFFFF
ALLOWED_DROP_MESSAGES = (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA)


def _windows_api(name: str, provided=None):
    if provided is not None:
        return provided
    return getattr(getattr(ctypes, "windll"), name)


def enable_windows_file_drop(
    window,
    *,
    platform: str | None = None,
    user32=None,
    shell32=None,
) -> bool:
    """Разрешает Проводнику отправлять файлы в повышенное окно приложения."""
    if (platform or sys.platform) != "win32":
        return False

    try:
        user32 = _windows_api("user32", user32)
        shell32 = _windows_api("shell32", shell32)
        hwnd_value = int(window.winId())
        hwnd = wintypes.HWND(hwnd_value)
        all_messages_allowed = True
        for message in ALLOWED_DROP_MESSAGES:
            allowed = bool(
                user32.ChangeWindowMessageFilterEx(
                    hwnd,
                    wintypes.UINT(message),
                    wintypes.DWORD(MSGFLT_ALLOW),
                    None,
                )
            )
            all_messages_allowed = allowed and all_messages_allowed
        shell32.DragAcceptFiles(hwnd, wintypes.BOOL(True))
        if not all_messages_allowed:
            log(
                "Windows не разрешила все сообщения для импорта перетаскиванием",
                "WARNING",
            )
        return all_messages_allowed
    except Exception as exc:
        log(f"Не удалось включить системный импорт перетаскиванием: {exc}", "ERROR")
        return False


def windows_dropped_file_paths(
    message,
    *,
    platform: str | None = None,
    shell32=None,
) -> list[str] | None:
    """Читает пути из WM_DROPFILES; для другого сообщения возвращает None."""
    if (platform or sys.platform) != "win32":
        return None

    try:
        native_message = wintypes.MSG.from_address(int(message))
    except Exception:
        return None
    if int(native_message.message) != WM_DROPFILES:
        return None

    drop_handle_value = int(native_message.wParam)
    drop_handle = wintypes.HANDLE(drop_handle_value)
    try:
        shell32 = _windows_api("shell32", shell32)
        count = int(
            shell32.DragQueryFileW(
                drop_handle,
                wintypes.UINT(DROP_FILE_COUNT_INDEX),
                None,
                wintypes.UINT(0),
            )
        )
        paths: list[str] = []
        for index in range(max(0, count)):
            required_length = int(
                shell32.DragQueryFileW(
                    drop_handle,
                    wintypes.UINT(index),
                    None,
                    wintypes.UINT(0),
                )
            )
            buffer = ctypes.create_unicode_buffer(required_length + 1)
            copied_length = int(
                shell32.DragQueryFileW(
                    drop_handle,
                    wintypes.UINT(index),
                    buffer,
                    wintypes.UINT(len(buffer)),
                )
            )
            if copied_length > 0 and buffer.value:
                paths.append(buffer.value)
        return paths
    except Exception as exc:
        log(f"Не удалось прочитать перетащенные Windows-файлы: {exc}", "ERROR")
        return []
    finally:
        try:
            if shell32 is None:
                shell32 = _windows_api("shell32")
            shell32.DragFinish(drop_handle)
        except Exception as exc:
            log(f"Не удалось освободить данные перетаскивания Windows: {exc}", "DEBUG")


__all__ = [
    "ALLOWED_DROP_MESSAGES",
    "DROP_FILE_COUNT_INDEX",
    "MSGFLT_ALLOW",
    "WM_COPYDATA",
    "WM_COPYGLOBALDATA",
    "WM_DROPFILES",
    "enable_windows_file_drop",
    "windows_dropped_file_paths",
]
