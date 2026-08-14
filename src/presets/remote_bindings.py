"""Привязки пресетов к удалённым источникам (URL).

Источник истины — секция ``remote_presets`` settings store; файлы
пресетов никаких служебных параметров не содержат. Привязка хранится
по file_name внутри scope движка (winws1/winws2).
"""

from __future__ import annotations

from typing import Any

from settings import store as settings_store
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2

REMOTE_PRESET_SCOPES = (ENGINE_WINWS2, ENGINE_WINWS1)


def _normalize_scope(scope_key: str) -> str:
    scope = str(scope_key or "").strip().lower()
    return scope if scope in REMOTE_PRESET_SCOPES else ENGINE_WINWS2


def _normalize_file_name(file_name: str) -> str:
    return str(file_name or "").strip()


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


def load_remote_preset_bindings(scope_key: str) -> dict[str, dict[str, Any]]:
    scope = _normalize_scope(scope_key)
    section = settings_store.get_remote_presets_settings()
    bindings = section.get(scope) if isinstance(section, dict) else None
    return dict(bindings) if isinstance(bindings, dict) else {}


def get_remote_preset_binding(scope_key: str, file_name: str) -> dict[str, Any] | None:
    key = _normalize_file_name(file_name)
    if not key:
        return None
    return load_remote_preset_bindings(scope_key).get(key)


def set_remote_preset_binding(scope_key: str, file_name: str, binding: dict[str, Any]) -> dict[str, Any] | None:
    scope = _normalize_scope(scope_key)
    key = _normalize_file_name(file_name)
    if not key or not isinstance(binding, dict) or not str(binding.get("url") or "").strip():
        return None
    section = settings_store.get_remote_presets_settings()
    if not isinstance(section, dict):
        section = {}
    scope_bindings = section.get(scope)
    if not isinstance(scope_bindings, dict):
        scope_bindings = {}
    scope_bindings[key] = dict(binding)
    section[scope] = scope_bindings
    saved = settings_store.set_remote_presets_settings(section)
    return saved.get(scope, {}).get(key)


def update_remote_preset_binding(scope_key: str, file_name: str, **fields) -> dict[str, Any] | None:
    current = get_remote_preset_binding(scope_key, file_name)
    if current is None:
        return None
    current.update(fields)
    return set_remote_preset_binding(scope_key, file_name, current)


def delete_remote_preset_binding(scope_key: str, file_name: str) -> bool:
    scope = _normalize_scope(scope_key)
    key = _normalize_file_name(file_name)
    if not key:
        return False
    section = settings_store.get_remote_presets_settings()
    scope_bindings = section.get(scope) if isinstance(section, dict) else None
    if not isinstance(scope_bindings, dict) or key not in scope_bindings:
        return False
    scope_bindings.pop(key, None)
    section[scope] = scope_bindings
    settings_store.set_remote_presets_settings(section)
    return True


def rename_remote_preset_binding(scope_key: str, old_file_name: str, new_file_name: str) -> bool:
    scope = _normalize_scope(scope_key)
    old_key = _normalize_file_name(old_file_name)
    new_key = _normalize_file_name(new_file_name)
    if not old_key or not new_key or old_key == new_key:
        return False
    section = settings_store.get_remote_presets_settings()
    scope_bindings = section.get(scope) if isinstance(section, dict) else None
    if not isinstance(scope_bindings, dict) or old_key not in scope_bindings:
        return False
    scope_bindings[new_key] = scope_bindings.pop(old_key)
    section[scope] = scope_bindings
    settings_store.set_remote_presets_settings(section)
    return True


def find_remote_preset_by_url(url: str) -> tuple[str, str, dict[str, Any]] | None:
    """Ищет привязанный пресет по URL во всех scope (дедупликация импорта)."""
    needle = str(url or "").strip()
    if not needle:
        return None
    for scope in REMOTE_PRESET_SCOPES:
        for file_name, binding in load_remote_preset_bindings(scope).items():
            if str(binding.get("url") or "").strip() == needle:
                return scope, file_name, binding
    return None
