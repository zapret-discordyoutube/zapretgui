from __future__ import annotations

"""Пути состояния обновления, переживающие переустановку приложения.

Установщик заменяет каталог установки целиком, а неудачное обновление может
его и вовсе опустошить. Всё, что нужно для восстановления и разбора аварии —
сохранённый установщик, состояние передачи управления, логи установщика и
наблюдателя, — поэтому лежит вне ``{app}``.

Модуль не импортирует Qt: им пользуются и конвейер обновления, и наблюдатель,
который работает уже после закрытия приложения.
"""

import os
from pathlib import Path

from config.build_info import CHANNEL
from config.runtime_layout import APPLICATION_PATHS, resolve_update_state_dir
from log.log import log


CACHED_INSTALLER_NAME = "Zapret2Setup.exe"
CACHED_INSTALLER_META_NAME = "installer.json"
HANDOFF_STATE_NAME = "handoff.json"
SETUP_LOG_NAME = "setup.log"
WATCHDOG_SCRIPT_NAME = "update_watchdog.ps1"
WATCHDOG_LOG_NAME = "watchdog.log"

_resolved_state_dir: Path | None = None


def preferred_update_state_dir() -> Path:
    """Каталог, который выбран по текущему окружению, без создания на диске."""
    return resolve_update_state_dir(
        channel=CHANNEL,
        application_root=APPLICATION_PATHS.root,
        program_data=os.environ.get("ProgramData"),
        local_app_data=os.environ.get("LOCALAPPDATA"),
    )


def update_state_dir() -> Path:
    """Существующий каталог состояния обновления.

    Выбор кэшируется: пути, отданные разным участникам обновления, обязаны
    совпадать даже если окружение изменится в середине работы.
    """
    global _resolved_state_dir

    if _resolved_state_dir is not None:
        return _resolved_state_dir

    fallback = Path(APPLICATION_PATHS.update_cache_dir)
    candidates = [preferred_update_state_dir()]
    if candidates[0] != fallback:
        candidates.append(fallback)

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log(f"Каталог обновления недоступен ({candidate}): {exc}", "WARNING")
            continue
        _resolved_state_dir = candidate
        return candidate

    _resolved_state_dir = fallback
    return fallback


def reset_update_state_dir_cache() -> None:
    """Сбрасывает выбранный каталог. Только для тестов."""
    global _resolved_state_dir

    _resolved_state_dir = None


def cached_installer_path() -> Path:
    return update_state_dir() / CACHED_INSTALLER_NAME


def cached_installer_meta_path() -> Path:
    return update_state_dir() / CACHED_INSTALLER_META_NAME


def handoff_state_path() -> Path:
    return update_state_dir() / HANDOFF_STATE_NAME


def setup_log_path(name: str = SETUP_LOG_NAME) -> Path:
    return update_state_dir() / (str(name or "").strip() or SETUP_LOG_NAME)


def watchdog_script_path() -> Path:
    return update_state_dir() / WATCHDOG_SCRIPT_NAME


def watchdog_log_path() -> Path:
    return update_state_dir() / WATCHDOG_LOG_NAME


__all__ = [
    "CACHED_INSTALLER_META_NAME",
    "CACHED_INSTALLER_NAME",
    "HANDOFF_STATE_NAME",
    "SETUP_LOG_NAME",
    "WATCHDOG_LOG_NAME",
    "WATCHDOG_SCRIPT_NAME",
    "cached_installer_meta_path",
    "cached_installer_path",
    "handoff_state_path",
    "preferred_update_state_dir",
    "reset_update_state_dir_cache",
    "setup_log_path",
    "update_state_dir",
    "watchdog_log_path",
    "watchdog_script_path",
]
