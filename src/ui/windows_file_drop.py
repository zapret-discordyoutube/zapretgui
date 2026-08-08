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
S_OK = 0
DRAGDROP_E_NOTREGISTERED = 0x80040100
DRAGDROP_E_ALREADYREGISTERED = 0x80040101
QT_DROP_TARGET_PROPERTY = "OleDropTargetInterface"
_DROP_TARGET_STATE_ATTR = "_windows_qt_drop_target_state"


def use_qt_file_drop(*, platform: str | None = None) -> bool:
    """Qt/OLE нельзя оставлять владельцем drop-зоны повышенного Windows-окна.

    Проводник не передаёт OLE drag-and-drop из обычного процесса в процесс с
    правами администратора. На Windows окно поэтому принимает файлы только
    через разрешённый ``WM_DROPFILES``; на остальных системах остаётся
    штатный Qt drag-and-drop.
    """
    return (platform or sys.platform) != "win32"


def _windows_api(name: str, provided=None):
    if provided is not None:
        return provided
    return getattr(getattr(ctypes, "windll"), name)


def _hresult_code(value) -> int:
    return int(value or 0) & 0xFFFFFFFF


def _call_com_reference_method(pointer: int, method_index: int) -> int:
    interface = ctypes.c_void_p(int(pointer))
    vtable = ctypes.cast(
        interface,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    prototype = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    method = prototype(ctypes.c_ulong, ctypes.c_void_p)(vtable[method_index])
    return int(method(interface))


def _release_drop_target_state(state: dict[str, int]) -> None:
    pointer = int(state.get("pointer", 0) or 0)
    state["pointer"] = 0
    state["hwnd"] = 0
    if not pointer:
        return
    try:
        _call_com_reference_method(pointer, 2)
    except Exception as exc:
        log(f"Не удалось освободить Qt drop target: {exc}", "DEBUG")


def _drop_target_state(window) -> dict[str, int]:
    state = getattr(window, _DROP_TARGET_STATE_ATTR, None)
    if isinstance(state, dict):
        return state

    state = {"hwnd": 0, "pointer": 0}
    setattr(window, _DROP_TARGET_STATE_ATTR, state)
    destroyed = getattr(window, "destroyed", None)
    connect = getattr(destroyed, "connect", None)
    if callable(connect):
        connect(lambda *_args, state=state: _release_drop_target_state(state))
    return state


def _remember_qt_drop_target(window, hwnd_value: int, user32) -> None:
    state = _drop_target_state(window)
    if int(state.get("hwnd", 0) or 0) != hwnd_value:
        _release_drop_target_state(state)

    get_property = getattr(user32, "GetPropW", None)
    if not callable(get_property) or int(state.get("pointer", 0) or 0):
        return

    try:
        get_property.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        get_property.restype = ctypes.c_void_p
    except Exception:
        pass
    pointer = int(get_property(wintypes.HWND(hwnd_value), QT_DROP_TARGET_PROPERTY) or 0)
    if not pointer:
        return
    _call_com_reference_method(pointer, 1)
    state["hwnd"] = hwnd_value
    state["pointer"] = pointer


def enable_windows_file_drop(
    window,
    *,
    platform: str | None = None,
    user32=None,
    shell32=None,
    ole32=None,
) -> bool:
    """Разрешает Проводнику отправлять файлы в повышенное окно приложения."""
    if (platform or sys.platform) != "win32":
        return False

    try:
        user32 = _windows_api("user32", user32)
        shell32 = _windows_api("shell32", shell32)
        ole32 = _windows_api("ole32", ole32)
        hwnd_value = int(window.winId())
        hwnd = wintypes.HWND(hwnd_value)
        _remember_qt_drop_target(window, hwnd_value, user32)
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
        revoke_result = _hresult_code(ole32.RevokeDragDrop(hwnd))
        ole_target_disabled = revoke_result in {S_OK, DRAGDROP_E_NOTREGISTERED}
        if not all_messages_allowed:
            log(
                "Windows не разрешила все сообщения для импорта перетаскиванием",
                "WARNING",
            )
        if not ole_target_disabled:
            log(
                f"Windows не отключила Qt OLE drop target: 0x{revoke_result:08X}",
                "WARNING",
            )
        return all_messages_allowed and ole_target_disabled
    except Exception as exc:
        log(f"Не удалось включить системный импорт перетаскиванием: {exc}", "ERROR")
        return False


def restore_windows_qt_file_drop(
    window,
    *,
    platform: str | None = None,
    shell32=None,
    ole32=None,
) -> bool:
    """Временно возвращает Qt/OLE для внутреннего переноса строк preset-ов."""
    if (platform or sys.platform) != "win32":
        return False

    hwnd = None
    resolved_shell32 = None
    legacy_drop_disabled = False
    try:
        hwnd_value = int(window.winId())
        state = getattr(window, _DROP_TARGET_STATE_ATTR, None)
        if not isinstance(state, dict):
            return False
        if int(state.get("hwnd", 0) or 0) != hwnd_value:
            return False
        pointer = int(state.get("pointer", 0) or 0)
        if not pointer:
            return False

        resolved_shell32 = _windows_api("shell32", shell32)
        ole32 = _windows_api("ole32", ole32)
        hwnd = wintypes.HWND(hwnd_value)
        resolved_shell32.DragAcceptFiles(hwnd, wintypes.BOOL(False))
        legacy_drop_disabled = True
        register_result = _hresult_code(
            ole32.RegisterDragDrop(hwnd, ctypes.c_void_p(pointer))
        )
        restored = register_result in {S_OK, DRAGDROP_E_ALREADYREGISTERED}
        if not restored:
            resolved_shell32.DragAcceptFiles(hwnd, wintypes.BOOL(True))
            legacy_drop_disabled = False
        return restored
    except Exception as exc:
        if legacy_drop_disabled and resolved_shell32 is not None and hwnd is not None:
            try:
                resolved_shell32.DragAcceptFiles(hwnd, wintypes.BOOL(True))
            except Exception:
                pass
        log(f"Не удалось временно включить внутреннее перетаскивание: {exc}", "ERROR")
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
    "DRAGDROP_E_ALREADYREGISTERED",
    "DRAGDROP_E_NOTREGISTERED",
    "MSGFLT_ALLOW",
    "QT_DROP_TARGET_PROPERTY",
    "S_OK",
    "WM_COPYDATA",
    "WM_COPYGLOBALDATA",
    "WM_DROPFILES",
    "enable_windows_file_drop",
    "restore_windows_qt_file_drop",
    "use_qt_file_drop",
    "windows_dropped_file_paths",
]
