"""Привязки пресетов к удалённым источникам (URL).

Источник истины — таблицы ``presets`` и ``preset_remote_sources`` в
settings.sqlite3: у каждого пресета есть постоянный uid (``pid:<uuid4>``),
привязка держится за uid и переживает переименование файла (меняется
только колонка file_name). Файлы пресетов никаких служебных параметров
не содержат. Публичный API этого модуля по-прежнему оперирует парой
(scope, file_name) — остальному коду uid знать не нужно.
"""

from __future__ import annotations

from typing import Any

from settings import store as settings_store
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2

REMOTE_PRESET_SCOPES = (ENGINE_WINWS2, ENGINE_WINWS1)

_LEGACY_SECTION_MIGRATED = False


def _normalize_scope(scope_key: str) -> str:
    scope = str(scope_key or "").strip().lower()
    return scope if scope in REMOTE_PRESET_SCOPES else ENGINE_WINWS2


def _migrate_legacy_section_once() -> None:
    """Одноразовый перенос привязок из старой JSON-секции remote_presets."""
    global _LEGACY_SECTION_MIGRATED
    if _LEGACY_SECTION_MIGRATED:
        return
    _LEGACY_SECTION_MIGRATED = True
    try:
        section = settings_store.get_remote_presets_settings()
    except Exception:
        return
    if not isinstance(section, dict):
        return
    migrated_any = False
    for scope in REMOTE_PRESET_SCOPES:
        legacy = section.get(scope)
        if not isinstance(legacy, dict) or not legacy:
            continue
        for file_name, binding in legacy.items():
            if not isinstance(binding, dict) or not str(binding.get("url") or "").strip():
                continue
            if settings_store.get_preset_remote_source(scope, file_name) is None:
                settings_store.set_preset_remote_source(scope, file_name, binding)
                migrated_any = True
    if migrated_any or any(section.get(scope) for scope in REMOTE_PRESET_SCOPES):
        try:
            settings_store.set_remote_presets_settings({scope: {} for scope in REMOTE_PRESET_SCOPES})
        except Exception:
            pass


def make_remote_preset_binding(url: str, *, synced_hash: str = "", now_iso: str = "") -> dict[str, Any]:
    return {
        "url": str(url or "").strip(),
        "etag": "",
        "last_modified": "",
        "synced_hash": str(synced_hash or ""),
        "checked_at": "",
        "updated_at": str(now_iso or ""),
        "error": "",
        "auto": True,
        "detached": False,
    }


def get_or_create_preset_uid(scope_key: str, file_name: str) -> str | None:
    return settings_store.get_or_create_preset_uid(_normalize_scope(scope_key), file_name)


def get_preset_uid(scope_key: str, file_name: str) -> str | None:
    return settings_store.get_preset_uid(_normalize_scope(scope_key), file_name)


def load_remote_preset_bindings(scope_key: str) -> dict[str, dict[str, Any]]:
    _migrate_legacy_section_once()
    return settings_store.list_preset_remote_sources(_normalize_scope(scope_key))


def get_remote_preset_binding(scope_key: str, file_name: str) -> dict[str, Any] | None:
    _migrate_legacy_section_once()
    return settings_store.get_preset_remote_source(_normalize_scope(scope_key), file_name)


def set_remote_preset_binding(scope_key: str, file_name: str, binding: dict[str, Any]) -> dict[str, Any] | None:
    _migrate_legacy_section_once()
    return settings_store.set_preset_remote_source(_normalize_scope(scope_key), file_name, binding)


def update_remote_preset_binding(scope_key: str, file_name: str, **fields) -> dict[str, Any] | None:
    current = get_remote_preset_binding(scope_key, file_name)
    if current is None:
        return None
    current.update(fields)
    return set_remote_preset_binding(scope_key, file_name, current)


def delete_remote_preset_binding(scope_key: str, file_name: str) -> bool:
    """Удаляет только привязку к источнику; uid пресета остаётся."""
    _migrate_legacy_section_once()
    return settings_store.delete_preset_remote_source(_normalize_scope(scope_key), file_name)


def rename_remote_preset_binding(scope_key: str, old_file_name: str, new_file_name: str) -> bool:
    """Переименование файла пресета: uid и привязка не двигаются."""
    _migrate_legacy_section_once()
    return settings_store.rename_preset_identity(
        _normalize_scope(scope_key), old_file_name, new_file_name
    )


def delete_preset_identity(scope_key: str, file_name: str) -> bool:
    """Пресет удалён: убирает uid, привязка уходит каскадом."""
    _migrate_legacy_section_once()
    return settings_store.delete_preset_identity(_normalize_scope(scope_key), file_name)


def find_remote_preset_by_url(url: str) -> tuple[str, str, dict[str, Any]] | None:
    """Ищет привязанный пресет по URL (дедупликация импорта)."""
    _migrate_legacy_section_once()
    return settings_store.find_preset_remote_source_by_url(url)
