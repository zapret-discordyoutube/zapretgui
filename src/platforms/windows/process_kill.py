"""
Остановка процесса по PID через Windows API (Windows-часть интерфейса platforms.processes).

Процессы движка winws здесь не останавливаются: для них есть
``winws_runtime.engine.process_control``, который завершает только процессы из
папки программы и подтверждает выход по хэндлу. Завершать winws по одному
лишь имени нельзя — так убивалась чужая копия запрета.
"""

import ctypes
from ctypes import wintypes
from log.log import log


# Windows API константы
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000

# WaitForSingleObject константы
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

if hasattr(ctypes, "windll"):
    kernel32 = ctypes.windll.kernel32

    OpenProcess = kernel32.OpenProcess
    OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    OpenProcess.restype = wintypes.HANDLE

    TerminateProcess = kernel32.TerminateProcess
    TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    TerminateProcess.restype = wintypes.BOOL

    WaitForSingleObject = kernel32.WaitForSingleObject
    WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    WaitForSingleObject.restype = wintypes.DWORD

    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - import safety for non-Windows environments
    kernel32 = None
    OpenProcess = None
    TerminateProcess = None
    WaitForSingleObject = None
    CloseHandle = None


def kill_process_by_pid_winapi(pid: int, wait_timeout_ms: int = 3000) -> bool:
    """Завершает процесс по PID только через WinAPI без fallback-веток."""
    try:
        if OpenProcess is None or TerminateProcess is None or WaitForSingleObject is None or CloseHandle is None:
            raise RuntimeError("WinAPI unavailable")
        h_process = OpenProcess(
            PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION | SYNCHRONIZE,
            False,
            pid
        )

        if h_process:
            try:
                result = TerminateProcess(h_process, 1)

                if result:
                    wait_result = WaitForSingleObject(h_process, wait_timeout_ms)

                    if wait_result == WAIT_OBJECT_0:
                        log(f"✅ Процесс PID={pid} завершён через Win API (подтверждено)", "DEBUG")
                        return True
                    elif wait_result == WAIT_TIMEOUT:
                        log(f"⚠ Процесс PID={pid}: TerminateProcess успешен, но процесс не завершился за {wait_timeout_ms}мс", "WARNING")
                    else:
                        log(f"⚠ Процесс PID={pid}: WaitForSingleObject вернул {wait_result}", "WARNING")

            finally:
                CloseHandle(h_process)
        else:
            log(f"Не удалось открыть процесс PID={pid} через WinAPI", "DEBUG")

    except Exception as e:
        log(f"Win API не сработал для PID={pid}: {e}", "DEBUG")

    return False
