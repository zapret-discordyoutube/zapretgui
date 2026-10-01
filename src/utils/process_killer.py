"""
Утилита для остановки процессов через Windows API.

Процессы движка winws здесь не останавливаются: для них есть
``winws_runtime.engine.process_control``, который завершает только процессы из
папки программы и подтверждает выход по хэндлу. Завершать winws по одному
лишь имени нельзя — так убивалась чужая копия запрета.
"""

import ctypes
from ctypes import wintypes
from log.log import log

from typing import List
from utils.windows_process_probe import iter_process_records_winapi

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


def kill_process_by_pid(pid: int, wait_timeout_ms: int = 3000) -> bool:
    """
    Завершает процесс по PID через Windows API.
    Ждёт реального завершения процесса.

    Args:
        pid: ID процесса
        wait_timeout_ms: Таймаут ожидания завершения в миллисекундах

    Returns:
        True если процесс успешно завершён
    """
    return kill_process_by_pid_winapi(pid, wait_timeout_ms=wait_timeout_ms)


def kill_process_by_name(process_name: str, kill_all: bool = True) -> int:
    """
    Завершает все процессы с указанным именем через Windows API.
    
    Args:
        process_name: Имя процесса
        kill_all: True для завершения всех найденных процессов
        
    Returns:
        Количество завершённых процессов
    """
    killed_count = 0
    process_name_lower = str(process_name or "").strip().lower()
    
    try:
        for pid, proc_name in iter_process_records_winapi():
            normalized = str(proc_name or "").strip().lower()
            if normalized != process_name_lower:
                continue

            if kill_process_by_pid(pid):
                killed_count += 1
                if not kill_all:
                    break
    except Exception as e:
        log(f"Ошибка поиска процесса {process_name}: {e}", "WARNING")
    
    if killed_count > 0:
        log(f"Завершено {killed_count} процессов {process_name}", "INFO")
    else:
        log(f"Процессы {process_name} не найдены или уже завершены", "DEBUG")
    
    return killed_count


def get_process_pids(process_name: str) -> List[int]:
    """
    Возвращает список PID всех процессов с указанным именем.
    
    Args:
        process_name: Имя процесса
        
    Returns:
        Список PID процессов
    """
    pids = []
    process_name_lower = str(process_name or "").strip().lower()
    
    try:
        for pid, proc_name in iter_process_records_winapi():
            normalized = str(proc_name or "").strip().lower()
            if normalized == process_name_lower:
                pids.append(int(pid))
    except Exception as e:
        log(f"Ошибка получения PID {process_name}: {e}", "DEBUG")
    
    return pids
