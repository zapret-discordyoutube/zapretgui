from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

from settings.mode import (
    ALL_WINWS_EXE_NAMES,
    EXE_NAME_WINWS1,
    EXE_NAME_WINWS2,
    ZAPRET1_MODE,
    ZAPRET2_MODE,
    exe_path_for_launch_method,
)
from utils.windows_process_probe import iter_process_records_winapi
from winws_runtime.engine.process_control import normalize_image_path as _normalize_path

_WINWS_NAMES = ALL_WINWS_EXE_NAMES
_WINWS_NAME_SET = frozenset(_WINWS_NAMES)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PROCESS_PATH = 32768


@dataclass(frozen=True, slots=True)
class WinwsProcessRecord:
    pid: int
    name: str
    exe_path: str


if hasattr(ctypes, "WinDLL"):
    # Приватный экземпляр WinDLL, а не глобальный ctypes.windll.kernel32:
    # windll кэширует функции процессно-глобально, и argtypes, выставленные
    # здесь, конфликтовали бы с argtypes других модулей на тех же функциях.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _OpenProcess = _kernel32.OpenProcess
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _OpenProcess.restype = wintypes.HANDLE

    _QueryFullProcessImageNameW = _kernel32.QueryFullProcessImageNameW
    _QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _QueryFullProcessImageNameW.restype = wintypes.BOOL

    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - import safety for non-Windows environments
    _OpenProcess = None
    _QueryFullProcessImageNameW = None
    _CloseHandle = None


def get_expected_winws_paths() -> dict[str, str]:
    paths = {
        EXE_NAME_WINWS1: _normalize_path(exe_path_for_launch_method(ZAPRET1_MODE)),
        EXE_NAME_WINWS2: _normalize_path(exe_path_for_launch_method(ZAPRET2_MODE)),
    }
    return {name: path for name, path in paths.items() if path}


def _iter_winws_process_entries() -> list[tuple[int, str]]:
    return [
        (int(pid), name)
        for pid, raw_name in iter_process_records_winapi()
        if (name := str(raw_name or "").strip().lower()) in _WINWS_NAME_SET
    ]


def _query_process_image_path(pid: int) -> str:
    if _OpenProcess is None or _QueryFullProcessImageNameW is None or _CloseHandle is None:
        return ""

    handle = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""

    try:
        buffer = ctypes.create_unicode_buffer(MAX_PROCESS_PATH)
        size = wintypes.DWORD(len(buffer))
        if not _QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return _normalize_path(buffer.value[: size.value] or buffer.value)
    finally:
        _CloseHandle(handle)


def find_expected_winws_processes(expected_exe_path: str) -> list[WinwsProcessRecord]:
    normalized_expected_path = _normalize_path(expected_exe_path)
    expected_name = os.path.basename(normalized_expected_path).strip().lower()

    if expected_name not in _WINWS_NAME_SET or not normalized_expected_path:
        return []
    if not os.path.exists(normalized_expected_path):
        return []

    matches: list[WinwsProcessRecord] = []
    for pid, process_name in _iter_winws_process_entries():
        if process_name != expected_name:
            continue
        process_path = _query_process_image_path(pid)
        if not process_path or process_path != normalized_expected_path:
            continue
        matches.append(
            WinwsProcessRecord(
                pid=int(pid),
                name=process_name,
                exe_path=process_path,
            )
        )

    matches.sort(key=lambda item: item.pid)
    return matches


def find_foreign_winws_processes() -> list[WinwsProcessRecord]:
    """winws-named processes that are NOT our canonical executables.

    A record counts as foreign only when its image path was successfully
    queried and differs from the expected canonical path for that name —
    an unqueryable path never raises a false alarm.
    """
    expected_paths = get_expected_winws_paths()
    foreign: list[WinwsProcessRecord] = []
    for pid, process_name in _iter_winws_process_entries():
        process_path = _query_process_image_path(pid)
        if not process_path:
            continue
        expected_path = expected_paths.get(process_name, "")
        if expected_path and process_path == expected_path:
            continue
        foreign.append(
            WinwsProcessRecord(
                pid=int(pid),
                name=process_name,
                exe_path=process_path,
            )
        )
    foreign.sort(key=lambda item: item.pid)
    return foreign


def find_canonical_winws_processes() -> dict[str, list[WinwsProcessRecord]]:
    result: dict[str, list[WinwsProcessRecord]] = {}
    for process_name, expected_path in get_expected_winws_paths().items():
        matches = find_expected_winws_processes(expected_path)
        if matches:
            result[process_name] = matches
    return result


def get_canonical_winws_process_pids() -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for process_name, records in find_canonical_winws_processes().items():
        pids = [record.pid for record in records if isinstance(record.pid, int)]
        if pids:
            result[process_name] = pids
    return result


def is_expected_winws_running(expected_exe_path: str) -> bool:
    return bool(find_expected_winws_processes(expected_exe_path))


def is_any_canonical_winws_running() -> bool:
    return bool(get_canonical_winws_process_pids())


def is_winws_process_pid_alive(pid: int, expected_name: str = "") -> bool:
    try:
        target_pid = int(pid)
    except Exception:
        return False

    normalized_name = str(expected_name or "").strip().lower()
    for current_pid, process_name in _iter_winws_process_entries():
        if int(current_pid) != target_pid:
            continue
        if normalized_name and str(process_name or "").strip().lower() != normalized_name:
            return False
        return True
    return False
