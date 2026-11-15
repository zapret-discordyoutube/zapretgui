from __future__ import annotations

from platforms.base import SystemSupport
from platforms.windows.system_support import WINDOWS_VERSION_ERROR_TITLE, current_windows_support

# Seconds between the FILETIME epoch (1601-01-01) and the Unix epoch (1970-01-01).
_FILETIME_EPOCH_OFFSET_SECONDS = 11_644_473_600
_FILETIME_TICKS_PER_SECOND = 10_000_000


def filetime_to_unix_seconds(filetime_ticks: int) -> float:
    return filetime_ticks / _FILETIME_TICKS_PER_SECOND - _FILETIME_EPOCH_OFFSET_SECONDS


class WindowsLaunch:
    """First-start facts on Windows. ctypes is imported inside the methods: this runs before
    anything else and the start-time measurement must not be shifted by heavy imports."""

    def process_creation_unix_seconds(self) -> float | None:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # Without argtypes ctypes would pass the pseudo-handle -1 as a 32-bit int,
            # and on x64 GetProcessTimes would get a broken HANDLE.
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            filetime_ptr = ctypes.POINTER(wintypes.FILETIME)
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                filetime_ptr,
                filetime_ptr,
                filetime_ptr,
                filetime_ptr,
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL

            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                kernel32.GetCurrentProcess(),
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            if not ok:
                return None
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return filetime_to_unix_seconds(ticks)
        except Exception:
            return None

    def system_support(self) -> SystemSupport:
        result = current_windows_support()
        if result.supported:
            return SystemSupport(supported=True)
        return SystemSupport(
            supported=False,
            os_name=result.os_name,
            title=WINDOWS_VERSION_ERROR_TITLE,
            message=result.message,
        )

    def source_launch_message(self) -> str:
        return "Запуск приложения из исходников запрещён. Используйте установленный _internal\\Zapret.exe."
