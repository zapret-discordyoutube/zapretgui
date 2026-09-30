from __future__ import annotations

"""Пути состояния обновления, переживающие переустановку приложения.

Установщик заменяет каталог установки целиком, а неудачное обновление может
его и вовсе опустошить. Всё, что нужно для восстановления и разбора аварии —
сохранённый установщик, состояние передачи управления, логи установщика и
наблюдателя, — поэтому лежит вне ``{app}``.

Модуль не импортирует Qt: им пользуются и конвейер обновления, и починка.
"""

import os
import subprocess
from pathlib import Path

from config.build_info import CHANNEL
from config.runtime_layout import APPLICATION_PATHS, resolve_update_state_dir
from log.log import log


CACHED_INSTALLER_NAME = "Zapret2Setup.exe"
CACHED_INSTALLER_META_NAME = "installer.json"
HANDOFF_STATE_NAME = "handoff.json"
SETUP_LOG_NAME = "setup.log"
WATCHDOG_SCRIPT_NAME = "watchdog.ps1"
WATCHDOG_LOG_NAME = "watchdog.log"
# Окно-продолжение: держит окно обновления на экране, пока старая программа
# закрыта, а новая ещё не открылась (см. ``splash``).
RESTART_SPLASH_SCRIPT_NAME = "restart_splash.ps1"
RESTART_SPLASH_SPEC_NAME = "restart_splash.json"
RESTART_SPLASH_LOGO_NAME = "restart_splash_logo.png"
RESTART_SPLASH_SHOWN_NAME = "restart_splash.shown"
RESTART_SPLASH_LOG_NAME = "restart_splash.log"
UPDATE_APP_READY_NAME = "app_ready.json"
# Имя скрипта наблюдателя в прежних версиях. Нужно только затем, чтобы найти
# и остановить зависший старый наблюдатель после первого обновления.
LEGACY_WATCHDOG_SCRIPT_NAME = "update_watchdog.ps1"

# Хорошо известные SID: они одинаковы в любой локализации Windows.
_SID_SYSTEM = "*S-1-5-18"
_SID_ADMINISTRATORS = "*S-1-5-32-544"
_SID_USERS = "*S-1-5-32-545"

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


def build_harden_commands(directory: str | Path) -> tuple[tuple[str, ...], ...]:
    """Команды icacls, закрывающие каталог от записи обычными пользователями.

    По умолчанию в ``%ProgramData%`` любой пользователь может создавать файлы
    и становится их владельцем. Установщик и наблюдатель отсюда запускаются с
    правами администратора, поэтому заранее подложенный файл превращался бы в
    повышение прав. Порядок важен:

    1. сам каталог сразу получает явный список прав вместо наследуемого:
       запись — системе и администраторам, пользователям — чтение. С этого
       момента новые файлы подложить нельзя;
    2. владельцем всего содержимого становятся администраторы — иначе
       владелец подложенного файла вернул бы себе доступ;
    3. права содержимого (``каталог\\*``) сбрасываются к наследуемым от
       каталога: явные разрешения подложенных файлов исчезают. Этот шаг
       выполняется, только если в каталоге что-то есть.

    ``/T`` на первом шаге нельзя: он переписывает и файлы, а флаги
    наследования ``(OI)(CI)`` к файлу не применяются — файл остаётся с пустым
    списком прав, и прочитать его не может даже администратор. Сбрасывать
    сам каталог тоже нельзя: на мгновение вернулось бы право записи для всех.
    """
    target = str(directory)
    return (
        (
            "icacls",
            target,
            "/inheritance:r",
            "/grant:r",
            f"{_SID_SYSTEM}:(OI)(CI)F",
            f"{_SID_ADMINISTRATORS}:(OI)(CI)F",
            f"{_SID_USERS}:(OI)(CI)RX",
            "/C",
            "/Q",
        ),
        ("icacls", target, "/setowner", _SID_ADMINISTRATORS, "/T", "/C", "/Q"),
        ("icacls", str(Path(target) / "*"), "/reset", "/T", "/C", "/Q"),
    )


def _is_inside_program_data(target: Path) -> bool:
    program_data = str(os.environ.get("ProgramData") or "").strip()
    if not program_data:
        return False
    base = os.path.normcase(os.path.abspath(program_data))
    candidate = os.path.normcase(os.path.abspath(str(target)))
    try:
        return os.path.commonpath([base, candidate]) == base
    except ValueError:
        return False


def harden_state_dir(directory: str | Path | None = None) -> bool:
    """Закрывает каталог состояния от записи обычными пользователями.

    Вызывается перед тем, как приложение что-то пишет в каталог или
    доверяет лежащему там установщику. Права сохраняются на диске, поэтому
    повторный вызов лишь подтверждает их.
    """
    if os.name != "nt":
        return True
    target = Path(directory) if directory is not None else update_state_dir()
    if not _is_inside_program_data(target):
        # %LOCALAPPDATA% и так принадлежит одному пользователю, а запасной
        # user\update_cache наследует права каталога установки.
        return True
    commands = build_harden_commands(target)
    try:
        has_children = any(target.iterdir())
    except OSError:
        has_children = True
    if not has_children:
        commands = commands[:-1]
    for command in commands:
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"Не удалось закрыть каталог обновления от записи: {exc}", "WARNING")
            return False
        if completed.returncode != 0:
            log(
                f"icacls не смог закрыть каталог обновления (код {completed.returncode})",
                "WARNING",
            )
            return False
    return True


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


def legacy_watchdog_script_path() -> Path:
    return update_state_dir() / LEGACY_WATCHDOG_SCRIPT_NAME


def watchdog_log_path() -> Path:
    return update_state_dir() / WATCHDOG_LOG_NAME


def restart_splash_script_path() -> Path:
    return update_state_dir() / RESTART_SPLASH_SCRIPT_NAME


def restart_splash_spec_path() -> Path:
    return update_state_dir() / RESTART_SPLASH_SPEC_NAME


def restart_splash_logo_path() -> Path:
    return update_state_dir() / RESTART_SPLASH_LOGO_NAME


def restart_splash_shown_path() -> Path:
    return update_state_dir() / RESTART_SPLASH_SHOWN_NAME


def restart_splash_log_path() -> Path:
    return update_state_dir() / RESTART_SPLASH_LOG_NAME


def update_app_ready_path() -> Path:
    return update_state_dir() / UPDATE_APP_READY_NAME


__all__ = [
    "CACHED_INSTALLER_META_NAME",
    "CACHED_INSTALLER_NAME",
    "HANDOFF_STATE_NAME",
    "LEGACY_WATCHDOG_SCRIPT_NAME",
    "RESTART_SPLASH_LOGO_NAME",
    "RESTART_SPLASH_LOG_NAME",
    "RESTART_SPLASH_SCRIPT_NAME",
    "RESTART_SPLASH_SHOWN_NAME",
    "RESTART_SPLASH_SPEC_NAME",
    "SETUP_LOG_NAME",
    "UPDATE_APP_READY_NAME",
    "WATCHDOG_LOG_NAME",
    "WATCHDOG_SCRIPT_NAME",
    "build_harden_commands",
    "cached_installer_meta_path",
    "cached_installer_path",
    "handoff_state_path",
    "harden_state_dir",
    "legacy_watchdog_script_path",
    "preferred_update_state_dir",
    "reset_update_state_dir_cache",
    "restart_splash_log_path",
    "restart_splash_logo_path",
    "restart_splash_script_path",
    "restart_splash_shown_path",
    "restart_splash_spec_path",
    "setup_log_path",
    "update_app_ready_path",
    "update_state_dir",
    "watchdog_log_path",
    "watchdog_script_path",
]
