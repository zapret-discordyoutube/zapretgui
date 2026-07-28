from __future__ import annotations

"""Страховка обновления на уровне системы.

Наблюдатель обновления закрывает почти все сценарии, но не тот, в котором
убиты и приложение, и сам наблюдатель — выключение питания, BSOD, принудительная
перезагрузка посреди установки. Запись в ``RunOnce`` переживает и это: при
следующем входе в систему восстановление проверит, стоит ли ожидаемая версия,
и при необходимости доведёт установку.

Запись ставится до начала установки и снимается наблюдателем только после
подтверждённого успеха.
"""

import subprocess
import winreg
from collections.abc import Sequence

from log.log import log


RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_VALUE_NAME = "ZapretUpdateRecovery"


def build_recovery_command(command: Sequence[str]) -> str:
    """Собирает командную строку так, как её прочитает RunOnce."""
    return subprocess.list2cmdline([str(part) for part in command])


def set_recovery_hook(command: Sequence[str] | str) -> bool:
    """Ставит запись восстановления. False, если реестр недоступен."""
    command_line = command if isinstance(command, str) else build_recovery_command(command)
    if not str(command_line).strip():
        return False

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            RUNONCE_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, RUNONCE_VALUE_NAME, 0, winreg.REG_SZ, str(command_line))
    except OSError as exc:
        log(f"Не удалось поставить страховку обновления: {exc}", "WARNING")
        return False

    log("Страховка обновления поставлена на следующий вход в систему", "🔁 UPDATE")
    return True


def clear_recovery_hook() -> bool:
    """Снимает запись восстановления. True, если её больше нет."""
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            RUNONCE_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, RUNONCE_VALUE_NAME)
    except FileNotFoundError:
        return True
    except OSError as exc:
        log(f"Не удалось снять страховку обновления: {exc}", "WARNING")
        return False
    return True


__all__ = [
    "RUNONCE_KEY",
    "RUNONCE_VALUE_NAME",
    "build_recovery_command",
    "clear_recovery_hook",
    "set_recovery_hook",
]
