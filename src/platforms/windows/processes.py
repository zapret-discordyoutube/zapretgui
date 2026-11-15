from __future__ import annotations

import ctypes
from ctypes import wintypes

from platforms.base import NoWorkingSet, ProcessSnapshotError  # noqa: F401

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PROCESS_PATH = 32768
# FILETIME counts 100-nanosecond ticks since 1601; this many come before the Unix epoch.
_FILETIME_UNIX_EPOCH = 116444736000000000

if hasattr(ctypes, "WinDLL"):
    # A private WinDLL instance, not the global ctypes.windll.kernel32: windll caches functions
    # process-wide, and argtypes set here would clash with argtypes other modules set on the same functions.
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

    _GetProcessTimes = _kernel32.GetProcessTimes
    _GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _GetProcessTimes.restype = wintypes.BOOL
else:  # pragma: no cover - import safety when the module is read on another system
    _OpenProcess = None
    _QueryFullProcessImageNameW = None
    _CloseHandle = None
    _GetProcessTimes = None


def filetime_to_unix_seconds(low: int, high: int) -> float:
    ticks = (int(high) << 32) | int(low)
    if ticks <= _FILETIME_UNIX_EPOCH:
        return 0.0
    return (ticks - _FILETIME_UNIX_EPOCH) / 10_000_000


def query_process_image_path(pid: int) -> str:
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
        return buffer.value[: size.value] or buffer.value
    finally:
        _CloseHandle(handle)


def query_process_start_time(pid: int) -> float:
    """When Windows created the process (Unix seconds); 0.0 when it could not be read."""
    if _OpenProcess is None or _GetProcessTimes is None or _CloseHandle is None:
        return 0.0

    handle = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return 0.0

    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return 0.0
        return filetime_to_unix_seconds(created.dwLowDateTime, created.dwHighDateTime)
    finally:
        _CloseHandle(handle)


class WindowsProcesses:
    """The Windows process functions of this folder behind the neutral interface.

    The modules are imported inside the methods: the interface is built at program start and the
    Toolhelp / ntdll tables should only be prepared when somebody really looks at processes.
    """

    def hidden_process_options(self) -> dict:
        import subprocess

        return {"creationflags": subprocess.CREATE_NO_WINDOW}

    def list_processes(self) -> list[tuple[int, str]]:
        from platforms.windows.process_probe import iter_process_records_winapi

        return iter_process_records_winapi()

    def list_processes_strict(self) -> list[tuple[int, str]]:
        from platforms.windows.process_probe import iter_process_records_winapi_strict

        return iter_process_records_winapi_strict()

    def command_line(self, pid: int) -> str:
        from platforms.windows.process_probe import process_command_line

        return process_command_line(pid)

    def exe_path(self, pid: int) -> str:
        return query_process_image_path(pid)

    def module_paths(self, pid: int, *, max_snapshot_attempts: int = 3) -> list[str]:
        from platforms.windows.process_probe import iter_process_module_paths_winapi

        return iter_process_module_paths_winapi(pid, max_snapshot_attempts=max_snapshot_attempts)

    def start_time(self, pid: int) -> float:
        return query_process_start_time(pid)

    def kill(self, pid: int, wait_timeout_ms: int = 3000) -> bool:
        from platforms.windows.process_kill import kill_process_by_pid_winapi

        return kill_process_by_pid_winapi(int(pid), wait_timeout_ms=wait_timeout_ms)


class WindowsWorkingSet:
    """Delegates to ``platforms.windows.working_set``; loaded on first use."""

    def __getattr__(self, name: str):
        from platforms.windows import working_set

        if name.startswith("_") or not hasattr(working_set, name):
            raise AttributeError(name)
        return getattr(working_set, name)
