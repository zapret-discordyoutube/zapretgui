"""
Legacy-ярлык автозагрузки ZapretGUI.lnk.

Раньше автозапуск GUI делался ярлыком в папке автозагрузки пользователя,
но Windows молча игнорирует такие ярлыки для программ с requireAdministrator
при включённом UAC. Теперь автозапуск идёт через Планировщик задач
(см. autostart/scheduled_task_api.py), а этот модуль оставлен только для
поиска и удаления старого ярлыка.
"""

from __future__ import annotations

import os
from pathlib import Path

from log.log import log


STARTUP_SHORTCUT_NAME = "ZapretGUI.lnk"
STARTUP_RELATIVE_PATH = (
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Startup",
)


def get_user_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata).joinpath(*STARTUP_RELATIVE_PATH)
    return Path.home().joinpath("AppData", "Roaming", *STARTUP_RELATIVE_PATH)


def get_startup_shortcut_path() -> Path:
    return get_user_startup_dir() / STARTUP_SHORTCUT_NAME


def delete_startup_shortcut(
    *,
    shortcut_path: str | os.PathLike[str] | None = None,
) -> bool:
    path = Path(shortcut_path) if shortcut_path is not None else get_startup_shortcut_path()
    try:
        if not path.exists():
            return False
        path.unlink()
        return True
    except Exception as exc:
        log(f"Startup shortcut delete failed: {exc}", "WARNING")
        return False
