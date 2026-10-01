# winws_runtime/health/windivert_auto_fix.py
"""Безопасные auto-fix действия для проблем WinDivert."""

import subprocess
from typing import Tuple


def execute_windivert_auto_fix(action: str) -> Tuple[bool, str]:
    """Execute an auto-fix action. Returns (success, message)."""
    if action == "enable_adapters":
        return _fix_enable_adapters()
    elif action == "enable_bfe":
        return _fix_enable_bfe()
    elif action == "cleanup_driver":
        return _fix_cleanup_driver()
    return False, f"Неизвестное действие: {action}"


def _fix_enable_adapters() -> Tuple[bool, str]:
    """Try to enable disabled network adapters."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Disabled'} | Enable-NetAdapter -Confirm:$false"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,
        )
        if result.returncode == 0:
            return True, "Сетевые адаптеры включены. Попробуйте запустить снова"
        return False, f"Не удалось включить адаптеры: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Ошибка: {e}"


def _fix_enable_bfe() -> Tuple[bool, str]:
    """Enable and start Base Filtering Engine service."""
    try:
        subprocess.run(
            ["sc", "config", "BFE", "start=", "auto"],
            capture_output=True, timeout=5, creationflags=0x08000000,
        )
        result = subprocess.run(
            ["net", "start", "BFE"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000,
        )
        if result.returncode == 0 or "already been started" in (result.stderr or "").lower():
            return True, "Служба BFE запущена. Попробуйте запустить снова"
        return False, f"Не удалось запустить BFE: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Ошибка: {e}"


def _fix_cleanup_driver() -> Tuple[bool, str]:
    """Останавливает свои winws и выгружает драйвер, если им никто не пользуется."""
    try:
        from winws_runtime.runtime.system_ops import (
            ensure_windivert_driver_startable_runtime,
            release_windivert_driver_runtime,
            stop_own_winws_processes_runtime,
        )

        if not stop_own_winws_processes_runtime():
            return False, "Не удалось завершить процессы winws. Перезагрузите компьютер"

        released = release_windivert_driver_runtime()
        if released.stuck:
            return False, released.message

        preflight = ensure_windivert_driver_startable_runtime()
        if not preflight.ok:
            return False, preflight.message

        return True, "Драйвер WinDivert освобождён. Попробуйте запустить снова"
    except Exception as e:
        return False, f"Ошибка очистки драйвера WinDivert: {e}"
