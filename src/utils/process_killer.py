"""
Остановка и поиск процессов по имени и PID. Как именно процесс завершается, решает
платформа (``platforms.current().processes``): на Windows это WinAPI, на Linux сигналы.

Процессы движка winws здесь не останавливаются: для них есть
``winws_runtime.engine.process_control``, который завершает только процессы из
папки программы и подтверждает выход по хэндлу. Завершать winws по одному
лишь имени нельзя — так убивалась чужая копия запрета.
"""

from typing import List

from log.log import log
from platforms import current as current_platform


def kill_process_by_pid(pid: int, wait_timeout_ms: int = 3000) -> bool:
    """Завершает процесс по PID и ждёт реального завершения. True — выход подтверждён."""
    return bool(current_platform().processes.kill(int(pid), wait_timeout_ms=wait_timeout_ms))


def kill_process_by_name(process_name: str, kill_all: bool = True) -> int:
    """Завершает процессы с указанным именем. Возвращает, сколько завершено."""
    killed_count = 0
    process_name_lower = str(process_name or "").strip().lower()

    try:
        for pid, proc_name in current_platform().processes.list_processes():
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
    """Возвращает PID всех процессов с указанным именем."""
    pids = []
    process_name_lower = str(process_name or "").strip().lower()

    try:
        for pid, proc_name in current_platform().processes.list_processes():
            normalized = str(proc_name or "").strip().lower()
            if normalized == process_name_lower:
                pids.append(int(pid))
    except Exception as e:
        log(f"Ошибка получения PID {process_name}: {e}", "DEBUG")

    return pids
