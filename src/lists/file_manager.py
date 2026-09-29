"""Утилиты для подготовки обязательных файлов приложения."""

import os
import re
from pathlib import Path

from lists.core.paths import get_lists_dir
from utils.atomic_text import read_preset_file_text

LISTS_FOLDER = get_lists_dir()

REQUIRED_RUNTIME_LIST_FILES = ("other.txt", "ipset-all.txt", "ipset-ru.txt")
_PRESET_LIST_FILE_RE = re.compile(
    r"^\s*--(?:hostlist|hostlist-exclude|ipset|ipset-exclude)\s*=\s*(?P<value>.+?)\s*$",
    flags=re.IGNORECASE,
)


def _runtime_required_file_ready(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _log(message: str, level: str) -> None:
    from log.log import log

    log(message, level)


def _rebuild_layered_final_lists(*, active_preset_path: str = "") -> int:
    from lists.core.layered_files import rebuild_all_layered_list_files

    return rebuild_all_layered_list_files(
        Path(LISTS_FOLDER),
        user_only_file_names=collect_live_user_only_list_file_names(active_preset_path=active_preset_path),
    )


def ensure_required_files_fast(*, active_preset_path: str = ""):
    """Быстро проверяет готовность итоговых списков для обычного запуска."""
    try:
        os.makedirs(LISTS_FOLDER, exist_ok=True)
        missing_files = [
            name
            for name in REQUIRED_RUNTIME_LIST_FILES
            if not _runtime_required_file_ready(os.path.join(LISTS_FOLDER, name))
        ]
        if not missing_files:
            rebuilt_count = _rebuild_layered_final_lists(active_preset_path=active_preset_path)
            _log(f"Итоговые списки проверены и пересобраны: {rebuilt_count}", "DEBUG")
            _log("Обязательные итоговые списки уже готовы", "DEBUG")
            return True

        _log(
            "Не найдены обязательные итоговые списки: "
            f"{', '.join(missing_files)}; выполняем полную подготовку",
            "WARNING",
        )
        if active_preset_path:
            return bool(ensure_required_files(active_preset_path=active_preset_path))
        return bool(ensure_required_files())
    except Exception as e:
        _log(f"Ошибка ensure_required_files_fast: {e}", "❌ ERROR")
        return False


def ensure_required_files(*, active_preset_path: str = ""):
    """Проверяет/подготавливает обязательные файлы списков."""
    try:
        os.makedirs(LISTS_FOLDER, exist_ok=True)

        from lists.hostlists_manager import ensure_hostlists_exist
        from lists.ipsets_manager import ensure_ipsets_exist
        from lists.core.layered_files import rebuild_all_layered_list_files

        hostlists_ok = ensure_hostlists_exist()
        ipsets_ok = ensure_ipsets_exist()
        rebuilt_count = rebuild_all_layered_list_files(
            LISTS_FOLDER,
            user_only_file_names=collect_live_user_only_list_file_names(active_preset_path=active_preset_path),
        )
        _log(f"Итоговые списки пересобраны: {rebuilt_count}", "DEBUG")

        result = bool(hostlists_ok and ipsets_ok)
        if result:
            _log("Обязательные файлы списков готовы", "DEBUG")
        else:
            _log(
                f"Не все обязательные файлы готовы: hostlists={hostlists_ok}, ipsets={ipsets_ok}",
                "WARNING",
            )
        return result
    except Exception as e:
        _log(f"Ошибка ensure_required_files: {e}", "❌ ERROR")
        return False


def collect_live_user_only_list_file_names(*, active_preset_path: str = "") -> set[str]:
    names = set(_user_profile_list_file_names())
    if active_preset_path:
        names.update(_preset_list_file_names(active_preset_path))
    return names


def _user_profile_list_file_names() -> set[str]:
    try:
        from lists.core.layered_files import safe_list_file_name
        from settings.store import get_user_profiles_settings

        settings = get_user_profiles_settings()
    except Exception:
        return set()

    profiles = settings.get("profiles") if isinstance(settings, dict) else {}
    if not isinstance(profiles, dict):
        return set()

    names: set[str] = set()
    for row in profiles.values():
        if not isinstance(row, dict):
            continue
        for key in ("hostlist", "ipset"):
            safe_name = safe_list_file_name(str(row.get(key) or ""))
            if safe_name:
                names.add(safe_name)
    return names


def _preset_list_file_names(active_preset_path: str) -> set[str]:
    try:
        from lists.core.layered_files import safe_list_file_name

        text = read_preset_file_text(active_preset_path)
    except Exception:
        return set()

    names: set[str] = set()
    for raw_line in text.splitlines():
        match = _PRESET_LIST_FILE_RE.match(raw_line)
        if not match:
            continue
        for raw_value in match.group("value").split(","):
            safe_name = safe_list_file_name(raw_value.strip().strip('"').strip("'").lstrip("@"))
            if safe_name:
                names.add(safe_name)
    return names
