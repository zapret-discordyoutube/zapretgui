from __future__ import annotations

import copy
import json
import sqlite3
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

try:
    import winreg
except ImportError:  # pragma: no cover - WSL/static checks only
    winreg = None

from config.runtime_layout import APPLICATION_PATHS
from settings.mode import (
    DEFAULT_LAUNCH_METHOD,
    ENGINE_WINWS1,
    ENGINE_WINWS2,
    SELECTED_SOURCE_PRESET_FILE_NAME_KEY_WINWS1,
    SELECTED_SOURCE_PRESET_FILE_NAME_KEY_WINWS2,
    normalize_launch_method,
)
from settings.normalize import (
    as_clean_str as _as_clean_str,
    as_dict as _as_dict,
    as_int as _as_int,
    normalize_askey as _normalize_askey,
    normalize_hex_secret as _normalize_hex_secret,
    normalize_settings as _normalize_settings,
    normalize_lookup_key as _normalize_lookup_key,
    unique_dc_ip_list as _unique_dc_ip_list,
    unique_domain_list as _unique_domain_list,
    unique_int_list as _unique_int_list,
    unique_str_list as _unique_str_list,
)
from settings.schema import (
    DEFAULT_TG_PROXY_HOST as _DEFAULT_TG_PROXY_HOST,
    DEFAULT_TG_PROXY_PORT as _DEFAULT_TG_PROXY_PORT,
    DEFAULT_TG_PROXY_UPSTREAM_PORT as _DEFAULT_TG_PROXY_UPSTREAM_PORT,
    DEFAULT_TINTED_INTENSITY as _DEFAULT_TINTED_INTENSITY,
    DEFAULT_WINDOW_OPACITY as _DEFAULT_WINDOW_OPACITY,
    SETTINGS_DATABASE_FILE_NAME as _SETTINGS_DATABASE_FILE_NAME,
    SETTINGS_DIR_NAME as _SETTINGS_DIR_NAME,
    TRAY_CLOSE_MODE_NORMAL as _TRAY_CLOSE_MODE_NORMAL,
    VALID_TRAY_CLOSE_MODES as _VALID_TRAY_CLOSE_MODES,
    build_default_settings as _build_default_settings,
)

_SETTINGS_LOCK = RLock()
_SETTINGS_CACHE: dict[str, Any] | None = None
_SETTINGS_CACHE_REVISION: int | None = None
_SETTINGS_CONNECTION: sqlite3.Connection | None = None
_SETTINGS_CONNECTION_PATH: Path | None = None
_SETTINGS_SCHEMA_VERSION = 1
_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings_sections (
    section TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO settings_meta(key, value) VALUES('revision', 0);
-- Постоянная идентичность пресетов: uid выдаётся один раз и не меняется,
-- переименование файла меняет только file_name. UNIQUE не даёт выдать
-- два uid одному файлу.
CREATE TABLE IF NOT EXISTS presets (
    uid TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    file_name TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    UNIQUE(scope, file_name)
);
-- Привязка пресета к удалённому источнику; живёт и умирает вместе с uid.
CREATE TABLE IF NOT EXISTS preset_remote_sources (
    preset_uid TEXT PRIMARY KEY REFERENCES presets(uid) ON DELETE CASCADE,
    url TEXT NOT NULL,
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    synced_hash TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    auto INTEGER NOT NULL DEFAULT 1,
    detached INTEGER NOT NULL DEFAULT 0
);
-- Реестр идентичности профилей (uid ↔ последние известные имя/сигнатура).
-- position фиксирует порядок реестра: резолвер идентичности перебирает
-- кандидатов в этом порядке (важно для полных дублей профилей).
CREATE TABLE IF NOT EXISTS profile_identities (
    scope TEXT NOT NULL,
    uid TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    sig TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, uid)
);
"""

_PRESET_UID_PREFIX = "pid:"
_REMOTE_SOURCE_FIELDS = (
    "url",
    "etag",
    "last_modified",
    "synced_hash",
    "checked_at",
    "updated_at",
    "error",
    "auto",
    "detached",
)
_DIRECT_PRESET_SELECTION_PATHS = {
    ENGINE_WINWS1: ("program", SELECTED_SOURCE_PRESET_FILE_NAME_KEY_WINWS1),
    ENGINE_WINWS2: ("program", SELECTED_SOURCE_PRESET_FILE_NAME_KEY_WINWS2),
}

# Тесты подменяют только этот корень, чтобы не писать в живую settings.sqlite3.
# В установленном приложении значение всегда приходит из APPLICATION_PATHS.
MAIN_DIRECTORY = str(APPLICATION_PATHS.root)


def _settings_root() -> Path:
    return Path(MAIN_DIRECTORY)


def get_settings_database_path() -> Path:
    return _settings_root() / _SETTINGS_DIR_NAME / _SETTINGS_DATABASE_FILE_NAME


def _serialize_section(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _close_connection_locked() -> None:
    global _SETTINGS_CONNECTION, _SETTINGS_CONNECTION_PATH
    global _SETTINGS_CACHE, _SETTINGS_CACHE_REVISION

    connection = _SETTINGS_CONNECTION
    _SETTINGS_CONNECTION = None
    _SETTINGS_CONNECTION_PATH = None
    _SETTINGS_CACHE = None
    _SETTINGS_CACHE_REVISION = None
    if connection is not None:
        try:
            connection.close()
        except sqlite3.Error:
            pass


def _open_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.executescript(_SETTINGS_SCHEMA)
        quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()
        if quick_check is None or str(quick_check[0]).lower() != "ok":
            detail = quick_check[0] if quick_check is not None else "no result"
            raise sqlite3.DatabaseError(f"database disk image is malformed: {detail}")
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > _SETTINGS_SCHEMA_VERSION:
            raise RuntimeError(
                f"settings.sqlite3 schema {current_version} is newer than supported "
                f"{_SETTINGS_SCHEMA_VERSION}"
            )
        connection.execute(f"PRAGMA user_version={_SETTINGS_SCHEMA_VERSION}")
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise


def _backup_corrupt_database_locked(path: Path, error: Exception) -> None:
    timestamp = time.time_ns()
    backups: list[Path] = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(path) + suffix)
        if not source.exists():
            continue
        target = path.with_name(f"{path.name}.corrupt.{timestamp}{suffix}.bak")
        try:
            source.replace(target)
            backups.append(target)
        except OSError:
            pass
    try:
        from log.log import log

        target = ", ".join(str(item) for item in backups) or "копию сохранить не удалось"
        log(
            f"settings.sqlite3 повреждена ({error}); создана новая база, резерв: {target}",
            "ERROR",
        )
    except Exception:
        pass


def _is_corruption_error(error: Exception) -> bool:
    if isinstance(error, json.JSONDecodeError):
        return True
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "file is not a database",
            "database disk image is malformed",
            "database malformed",
            "malformed json",
        )
    )


def _read_revision_locked(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM settings_meta WHERE key='revision'"
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _read_settings_database_locked(connection: sqlite3.Connection) -> dict[str, Any]:
    return _normalize_settings(_read_raw_sections_locked(connection))


def _read_raw_sections_locked(connection: sqlite3.Connection) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for row in connection.execute("SELECT section, payload FROM settings_sections"):
        raw[str(row["section"])] = json.loads(str(row["payload"]))
    return raw


def _write_changed_sections_locked(
    connection: sqlite3.Connection,
    current: dict[str, Any],
    updated: dict[str, Any],
) -> bool:
    changed = False
    now_ms = int(time.time() * 1000)
    for section, value in updated.items():
        if current.get(section) == value and section in current:
            continue
        connection.execute(
            """
            INSERT INTO settings_sections(section, payload, updated_at_ms)
            VALUES(?, ?, ?)
            ON CONFLICT(section) DO UPDATE SET
                payload=excluded.payload,
                updated_at_ms=excluded.updated_at_ms
            """,
            (section, _serialize_section(value), now_ms),
        )
        changed = True
    obsolete = set(current) - set(updated)
    if obsolete:
        connection.executemany(
            "DELETE FROM settings_sections WHERE section=?",
            ((name,) for name in sorted(obsolete)),
        )
        changed = True
    if changed:
        connection.execute(
            "UPDATE settings_meta SET value=value+1 WHERE key='revision'"
        )
    return changed


def _initialize_database_locked(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = _read_raw_sections_locked(connection)
        normalized = _normalize_settings(current if current else _build_default_settings())
        _write_changed_sections_locked(connection, current, normalized)
        connection.commit()
        return normalized
    except Exception:
        connection.rollback()
        raise


def _create_connection_with_recovery_locked(path: Path) -> tuple[sqlite3.Connection, dict[str, Any]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_connection(path)
        return connection, _initialize_database_locked(connection)
    except Exception as error:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass
        if not _is_corruption_error(error):
            raise
        _backup_corrupt_database_locked(path, error)
        connection = _open_connection(path)
        return connection, _initialize_database_locked(connection)


def _get_connection_locked() -> sqlite3.Connection:
    global _SETTINGS_CONNECTION, _SETTINGS_CONNECTION_PATH
    global _SETTINGS_CACHE, _SETTINGS_CACHE_REVISION

    path = get_settings_database_path().resolve()
    if _SETTINGS_CONNECTION is not None and _SETTINGS_CONNECTION_PATH == path:
        return _SETTINGS_CONNECTION
    _close_connection_locked()
    connection, data = _create_connection_with_recovery_locked(path)
    _SETTINGS_CONNECTION = connection
    _SETTINGS_CONNECTION_PATH = path
    _SETTINGS_CACHE = copy.deepcopy(data)
    _SETTINGS_CACHE_REVISION = _read_revision_locked(connection)
    return connection


def _read_settings_cached_locked() -> dict[str, Any]:
    global _SETTINGS_CACHE, _SETTINGS_CACHE_REVISION

    connection = _get_connection_locked()
    revision = _read_revision_locked(connection)
    if _SETTINGS_CACHE is not None and _SETTINGS_CACHE_REVISION == revision:
        return _SETTINGS_CACHE
    data = _read_settings_database_locked(connection)
    _SETTINGS_CACHE = copy.deepcopy(data)
    _SETTINGS_CACHE_REVISION = revision
    return _SETTINGS_CACHE


def read_settings() -> dict[str, Any]:
    with _SETTINGS_LOCK:
        return copy.deepcopy(_read_settings_cached_locked())


def prepare_settings_database() -> dict[str, Any]:
    """Создаёт и проверяет каноническую SQLite-базу настроек."""
    with _SETTINGS_LOCK:
        return copy.deepcopy(_read_settings_cached_locked())


def close_settings_database() -> None:
    """Закрывает SQLite перед удалением каталога или заменой установки."""
    with _SETTINGS_LOCK:
        _close_connection_locked()


def get_settings_revision() -> int:
    """Возвращает монотонную ревизию для зависимых кэшей."""
    with _SETTINGS_LOCK:
        return _read_revision_locked(_get_connection_locked())


def reset_settings() -> dict[str, Any]:
    global _SETTINGS_CACHE, _SETTINGS_CACHE_REVISION

    data = _normalize_settings(_build_default_settings())
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = _read_settings_database_locked(connection)
            _write_changed_sections_locked(connection, current, data)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        _SETTINGS_CACHE = copy.deepcopy(data)
        _SETTINGS_CACHE_REVISION = _read_revision_locked(connection)
    return copy.deepcopy(data)


def _get_path_value(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _read_path_value(path: tuple[str, ...], default: Any = None) -> Any:
    """Читает одно значение, не копируя весь снимок настроек.

    `read_settings()` отдаёт глубокую копию всего документа — примерно 100 мкс
    на вызов. Геттеры настроек дёргаются из GUI-потока десятками за одну
    перерисовку, поэтому копия делается только когда значение действительно
    составное: скаляры неизменяемы и отдаются как есть.
    """
    with _SETTINGS_LOCK:
        value = _get_path_value(_read_settings_cached_locked(), path, default)
        if isinstance(value, (dict, list)):
            return copy.deepcopy(value)
        return value


def _read_section(name: str) -> Any:
    """Копирует одну секцию настроек вместо всего документа."""
    with _SETTINGS_LOCK:
        return copy.deepcopy(_read_settings_cached_locked()[name])


def _set_path_value(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = data
    for part in path[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[path[-1]] = value


def _update_settings(mutator) -> dict[str, Any]:
    global _SETTINGS_CACHE, _SETTINGS_CACHE_REVISION

    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Внутри write-транзакции перечитываем базу заново. Именно это
            # защищает read-modify-write от потери изменений другого процесса.
            current = _read_settings_database_locked(connection)
            working = copy.deepcopy(current)
            mutator(working)
            normalized = _normalize_settings(working)
            changed = _write_changed_sections_locked(connection, current, normalized)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        if changed:
            _SETTINGS_CACHE = copy.deepcopy(normalized)
            _SETTINGS_CACHE_REVISION = _read_revision_locked(connection)
        else:
            _SETTINGS_CACHE = copy.deepcopy(current)
            _SETTINGS_CACHE_REVISION = _read_revision_locked(connection)
        return copy.deepcopy(_SETTINGS_CACHE)


def _get_bool(path: tuple[str, ...], default: bool = False) -> bool:
    return bool(_read_path_value(path, default))


def _set_bool(path: tuple[str, ...], value: bool) -> bool:
    _update_settings(lambda data: _set_path_value(data, path, bool(value)))
    return True


def _get_int(path: tuple[str, ...], default: int = 0) -> int:
    return int(_read_path_value(path, default))


def _set_int(path: tuple[str, ...], value: int) -> bool:
    _update_settings(lambda data: _set_path_value(data, path, int(value)))
    return True


def _get_str(path: tuple[str, ...], default: str = "") -> str:
    return str(_read_path_value(path, default) or "")


def _set_str(path: tuple[str, ...], value: str) -> bool:
    _update_settings(lambda data: _set_path_value(data, path, str(value)))
    return True


def _get_str_in(path: tuple[str, ...], allowed: frozenset[str], default: str = "") -> str:
    value = _get_str(path, default)
    return value if value in allowed else default


def _set_str_in(path: tuple[str, ...], value: str, allowed: frozenset[str], default: str = "") -> bool:
    normalized = str(value or "")
    if normalized not in allowed:
        normalized = default
    return _set_str(path, normalized)


def _get_str_list(path: tuple[str, ...]) -> list[str]:
    value = _read_path_value(path, [])
    return list(value) if isinstance(value, list) else []


def _set_str_list(path: tuple[str, ...], value: object) -> bool:
    _update_settings(lambda data: _set_path_value(data, path, _unique_domain_list(value)))
    return True


def _set_dc_ip_list(path: tuple[str, ...], value: object) -> bool:
    _update_settings(lambda data: _set_path_value(data, path, _unique_dc_ip_list(value)))
    return True


def _get_nullable_str(path: tuple[str, ...]) -> str | None:
    value = _read_path_value(path, None)
    return value if isinstance(value, str) and value.strip() else None


def _set_nullable_str(path: tuple[str, ...], value: str | None) -> bool:
    _update_settings(lambda data: _set_path_value(data, path, None if value is None else str(value)))
    return True


def _presets_selection_path(engine: str) -> tuple[str, ...]:
    normalized = _as_clean_str(engine).lower()
    if normalized not in _DIRECT_PRESET_SELECTION_PATHS:
        raise ValueError(f"Unsupported preset selection engine: {engine}")
    return _DIRECT_PRESET_SELECTION_PATHS[normalized]


def get_program_settings() -> dict[str, Any]:
    return _read_section("program")


def set_program_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["program"].update(_as_dict(values)))
    return copy.deepcopy(updated["program"])


def get_window_settings() -> dict[str, Any]:
    return _read_section("window")


def set_window_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["window"].update(_as_dict(values)))
    return copy.deepcopy(updated["window"])


def get_appearance_settings() -> dict[str, Any]:
    return _read_section("appearance")


def set_appearance_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["appearance"].update(_as_dict(values)))
    return copy.deepcopy(updated["appearance"])


def get_warnings_settings() -> dict[str, Any]:
    return _read_section("warnings")


def set_warnings_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["warnings"].update(_as_dict(values)))
    return copy.deepcopy(updated["warnings"])


def get_telegram_proxy_settings() -> dict[str, Any]:
    return _read_section("telegram_proxy")


def set_telegram_proxy_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["telegram_proxy"].update(_as_dict(values)))
    return copy.deepcopy(updated["telegram_proxy"])


def get_dns_settings() -> dict[str, Any]:
    return _read_section("dns")


def set_dns_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["dns"].update(_as_dict(values)))
    return copy.deepcopy(updated["dns"])


def get_hosts_settings() -> dict[str, Any]:
    return _read_section("hosts")


def set_hosts_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["hosts"].update(_as_dict(values)))
    return copy.deepcopy(updated["hosts"])


def get_ui_state_settings() -> dict[str, Any]:
    return _read_section("ui_state")


def set_ui_state_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["ui_state"].update(_as_dict(values)))
    return copy.deepcopy(updated["ui_state"])


def get_profile_strategy_state_settings() -> dict[str, Any]:
    return _read_section("profile_strategy_state")


def set_profile_strategy_state_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: _set_path_value(data, ("profile_strategy_state",), _as_dict(values)))
    return copy.deepcopy(updated["profile_strategy_state"])


def get_user_profiles_settings() -> dict[str, Any]:
    return _read_section("user_profiles")


def set_user_profiles_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: _set_path_value(data, ("user_profiles",), _as_dict(values)))
    return copy.deepcopy(updated["user_profiles"])


def get_user_profiles_revision() -> str:
    """Детерминированный токен состояния user_profiles для ключей кэшей.

    Кэши, производные от пользовательских профилей, обязаны включать этот токен
    в ключ вместо ручной инвалидации: токен меняется при любой записи секции,
    в том числе из другого экземпляра сервиса или другого процесса.
    """
    with _SETTINGS_LOCK:
        data = _read_settings_cached_locked()
        payload = data.get("user_profiles") if isinstance(data, dict) else None
        return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)


def get_updater_settings() -> dict[str, Any]:
    return _read_section("updater")


def set_updater_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["updater"].update(_as_dict(values)))
    return copy.deepcopy(updated["updater"])


def get_last_seen_version() -> str:
    return _get_str(("program", "last_seen_version"), "")


def set_last_seen_version(value: str) -> bool:
    return _set_str(("program", "last_seen_version"), str(value or ""))


def get_self_repair_attempts() -> tuple[int, ...]:
    raw = _read_path_value(("updater", "self_repair", "attempts"), ())
    return tuple(int(item) for item in raw or () if isinstance(item, (int, float)))


def append_self_repair_attempt(
    *,
    max_attempts: int,
    window_seconds: int,
    now: float | None = None,
) -> tuple[bool, int]:
    """Резервирует попытку восстановления поставки под суточным лимитом.

    Решение и запись живут в одной транзакции настроек: иначе два запуска
    подряд могли бы одновременно увидеть свободный лимит.
    """
    import time as _time

    current = int(now if now is not None else _time.time())
    window_start = current - max(int(window_seconds), 0)
    allowed = False
    attempts_in_window = 0

    def _mutate(data: dict[str, Any]) -> None:
        nonlocal allowed, attempts_in_window

        raw = _get_path_value(data, ("updater", "self_repair", "attempts"), ()) or ()
        kept = sorted(
            int(item)
            for item in raw
            if isinstance(item, (int, float)) and int(item) > window_start
        )
        attempts_in_window = len(kept)
        allowed = attempts_in_window < max(int(max_attempts), 0)
        if allowed:
            kept.append(current)
            attempts_in_window = len(kept)
        _set_path_value(data, ("updater", "self_repair", "attempts"), kept)

    _update_settings(_mutate)
    return allowed, attempts_in_window


def reset_self_repair_attempts() -> None:
    _update_settings(lambda data: _set_path_value(data, ("updater", "self_repair", "attempts"), []))


def get_blockcheck_settings() -> dict[str, Any]:
    return _read_section("blockcheck")


def set_blockcheck_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["blockcheck"].update(_as_dict(values)))
    return copy.deepcopy(updated["blockcheck"])


def get_folders_settings() -> dict[str, Any]:
    return _read_section("folders")


def set_folders_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: _set_path_value(data, ("folders",), _as_dict(values)))
    return copy.deepcopy(updated["folders"])


def get_remote_presets_settings() -> dict[str, Any]:
    return _read_section("remote_presets")


def set_remote_presets_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: _set_path_value(data, ("remote_presets",), _as_dict(values)))
    return copy.deepcopy(updated["remote_presets"])


# ─────────────────────────────────────────────────────────────────
# Идентичность пресетов и привязки к удалённым источникам (таблицы)
# ─────────────────────────────────────────────────────────────────


def _generate_preset_uid() -> str:
    return f"{_PRESET_UID_PREFIX}{uuid.uuid4().hex}"


def _clean_scope_file(scope: str, file_name: str) -> tuple[str, str]:
    return str(scope or "").strip().lower(), str(file_name or "").strip()


def _remote_source_row_to_dict(row) -> dict[str, Any]:
    return {
        "url": str(row["url"]),
        "etag": str(row["etag"]),
        "last_modified": str(row["last_modified"]),
        "synced_hash": str(row["synced_hash"]),
        "checked_at": str(row["checked_at"]),
        "updated_at": str(row["updated_at"]),
        "error": str(row["error"]),
        "auto": bool(row["auto"]),
        "detached": bool(row["detached"]),
    }


def get_or_create_preset_uid(scope: str, file_name: str) -> str | None:
    scope, file_name = _clean_scope_file(scope, file_name)
    if not scope or not file_name:
        return None
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        row = connection.execute(
            "SELECT uid FROM presets WHERE scope=? AND file_name=?",
            (scope, file_name),
        ).fetchone()
        if row is not None:
            return str(row["uid"])
        uid = _generate_preset_uid()
        connection.execute(
            "INSERT OR IGNORE INTO presets(uid, scope, file_name, created_at_ms) VALUES(?,?,?,?)",
            (uid, scope, file_name, int(time.time() * 1000)),
        )
        # INSERT OR IGNORE: при гонке двух процессов выигрывает первый —
        # перечитываем, чтобы оба увидели один и тот же uid.
        row = connection.execute(
            "SELECT uid FROM presets WHERE scope=? AND file_name=?",
            (scope, file_name),
        ).fetchone()
        return str(row["uid"]) if row is not None else None


def get_preset_uid(scope: str, file_name: str) -> str | None:
    scope, file_name = _clean_scope_file(scope, file_name)
    if not scope or not file_name:
        return None
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        row = connection.execute(
            "SELECT uid FROM presets WHERE scope=? AND file_name=?",
            (scope, file_name),
        ).fetchone()
        return str(row["uid"]) if row is not None else None


def rename_preset_identity(scope: str, old_file_name: str, new_file_name: str) -> bool:
    scope, old_file_name = _clean_scope_file(scope, old_file_name)
    _, new_file_name = _clean_scope_file(scope, new_file_name)
    if not scope or not old_file_name or not new_file_name or old_file_name == new_file_name:
        return False
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        try:
            cursor = connection.execute(
                "UPDATE presets SET file_name=? WHERE scope=? AND file_name=?",
                (new_file_name, scope, old_file_name),
            )
        except sqlite3.IntegrityError:
            return False
        return cursor.rowcount > 0


def delete_preset_identity(scope: str, file_name: str) -> bool:
    """Удаляет пресет из реестра; привязка к источнику уходит каскадом."""
    scope, file_name = _clean_scope_file(scope, file_name)
    if not scope or not file_name:
        return False
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        cursor = connection.execute(
            "DELETE FROM presets WHERE scope=? AND file_name=?",
            (scope, file_name),
        )
        return cursor.rowcount > 0


def get_preset_remote_source(scope: str, file_name: str) -> dict[str, Any] | None:
    scope, file_name = _clean_scope_file(scope, file_name)
    if not scope or not file_name:
        return None
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        row = connection.execute(
            "SELECT s.* FROM preset_remote_sources s"
            " JOIN presets p ON p.uid = s.preset_uid"
            " WHERE p.scope=? AND p.file_name=?",
            (scope, file_name),
        ).fetchone()
        return _remote_source_row_to_dict(row) if row is not None else None


def list_preset_remote_sources(scope: str) -> dict[str, dict[str, Any]]:
    scope = str(scope or "").strip().lower()
    if not scope:
        return {}
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        rows = connection.execute(
            "SELECT p.file_name, s.* FROM preset_remote_sources s"
            " JOIN presets p ON p.uid = s.preset_uid"
            " WHERE p.scope=?",
            (scope,),
        ).fetchall()
        return {str(row["file_name"]): _remote_source_row_to_dict(row) for row in rows}


def set_preset_remote_source(scope: str, file_name: str, binding: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(binding, dict) or not str(binding.get("url") or "").strip():
        return None
    uid = get_or_create_preset_uid(scope, file_name)
    if uid is None:
        return None
    values = {
        "url": str(binding.get("url") or "").strip(),
        "etag": str(binding.get("etag") or ""),
        "last_modified": str(binding.get("last_modified") or ""),
        "synced_hash": str(binding.get("synced_hash") or ""),
        "checked_at": str(binding.get("checked_at") or ""),
        "updated_at": str(binding.get("updated_at") or ""),
        "error": str(binding.get("error") or ""),
        "auto": 1 if bool(binding.get("auto", True)) else 0,
        "detached": 1 if bool(binding.get("detached", False)) else 0,
    }
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        connection.execute(
            "INSERT INTO preset_remote_sources(preset_uid, url, etag, last_modified,"
            " synced_hash, checked_at, updated_at, error, auto, detached)"
            " VALUES(:uid, :url, :etag, :last_modified, :synced_hash, :checked_at,"
            " :updated_at, :error, :auto, :detached)"
            " ON CONFLICT(preset_uid) DO UPDATE SET"
            " url=:url, etag=:etag, last_modified=:last_modified, synced_hash=:synced_hash,"
            " checked_at=:checked_at, updated_at=:updated_at, error=:error,"
            " auto=:auto, detached=:detached",
            {"uid": uid, **values},
        )
    return get_preset_remote_source(scope, file_name)


def delete_preset_remote_source(scope: str, file_name: str) -> bool:
    scope, file_name = _clean_scope_file(scope, file_name)
    if not scope or not file_name:
        return False
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        cursor = connection.execute(
            "DELETE FROM preset_remote_sources WHERE preset_uid IN"
            " (SELECT uid FROM presets WHERE scope=? AND file_name=?)",
            (scope, file_name),
        )
        return cursor.rowcount > 0


def find_preset_remote_source_by_url(url: str) -> tuple[str, str, dict[str, Any]] | None:
    needle = str(url or "").strip()
    if not needle:
        return None
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        row = connection.execute(
            "SELECT p.scope, p.file_name, s.* FROM preset_remote_sources s"
            " JOIN presets p ON p.uid = s.preset_uid"
            " WHERE s.url=?",
            (needle,),
        ).fetchone()
        if row is None:
            return None
        return str(row["scope"]), str(row["file_name"]), _remote_source_row_to_dict(row)


def _normalize_identity_rows(registry: object) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not isinstance(registry, dict):
        return result
    for raw_uid, raw_meta in registry.items():
        uid = str(raw_uid or "").strip()
        if not uid or not isinstance(raw_meta, dict):
            continue
        result[uid] = {
            "name": str(raw_meta.get("name") or "").strip(),
            "sig": str(raw_meta.get("sig") or "").strip(),
        }
    return result


def get_profile_identity_registry(engine: str) -> dict[str, Any]:
    key = str(engine or "").strip().lower()
    if not key:
        return {}
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        rows = connection.execute(
            "SELECT uid, name, sig FROM profile_identities WHERE scope=? ORDER BY position",
            (key,),
        ).fetchall()
        if rows:
            return {str(row["uid"]): {"name": str(row["name"]), "sig": str(row["sig"])} for row in rows}
    # Ленивая миграция: реестр раньше жил JSON-секцией profile_identity.
    legacy = _normalize_identity_rows(_read_path_value(("profile_identity", key), None) or {})
    if legacy:
        set_profile_identity_registry(key, legacy)
    return legacy


def set_profile_identity_registry(engine: str, registry: dict[str, Any]) -> dict[str, Any]:
    key = str(engine or "").strip().lower()
    normalized = _normalize_identity_rows(registry)
    if not key:
        return {}
    with _SETTINGS_LOCK:
        connection = _get_connection_locked()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM profile_identities WHERE scope=?", (key,))
            connection.executemany(
                "INSERT INTO profile_identities(scope, uid, name, sig, position) VALUES(?,?,?,?,?)",
                [
                    (key, uid, meta["name"], meta["sig"], position)
                    for position, (uid, meta) in enumerate(normalized.items())
                ],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return copy.deepcopy(normalized)


def get_orchestra_settings() -> dict[str, Any]:
    return _read_path_value(("orchestra", "settings"), None) or {}


def set_orchestra_settings(values: dict[str, Any]) -> dict[str, Any]:
    updated = _update_settings(lambda data: data["orchestra"]["settings"].update(_as_dict(values)))
    return copy.deepcopy(updated["orchestra"]["settings"])


def get_dpi_autostart() -> bool:
    return _get_bool(("program", "dpi_autostart"), True)


def set_dpi_autostart(value: bool) -> bool:
    return _set_bool(("program", "dpi_autostart"), value)


def get_gui_autostart_enabled() -> bool:
    return _get_bool(("program", "gui_autostart_enabled"), False)


def set_gui_autostart_enabled(value: bool) -> bool:
    return _set_bool(("program", "gui_autostart_enabled"), value)


def get_strategy_launch_method() -> str:
    return normalize_launch_method(_get_str(("program", "strategy_launch_method"), DEFAULT_LAUNCH_METHOD))


def set_strategy_launch_method(value: str) -> bool:
    return _set_str(("program", "strategy_launch_method"), normalize_launch_method(value))


def get_selected_source_preset_file_name(engine: str) -> str | None:
    value = _get_str(_presets_selection_path(engine), "")
    return value or None


def set_selected_source_preset_file_name(engine: str, value: str | None) -> bool:
    normalized = _as_clean_str(value)
    return _set_str(_presets_selection_path(engine), normalized)


def clear_selected_source_preset_file_name(engine: str) -> bool:
    return _set_str(_presets_selection_path(engine), "")


def get_auto_update_enabled() -> bool:
    return _get_bool(("program", "auto_update_enabled"), True)


def set_auto_update_enabled(value: bool) -> bool:
    return _set_bool(("program", "auto_update_enabled"), value)


def get_discord_restart_enabled() -> bool:
    return _get_bool(("program", "discord_auto_restart"), True)


def set_discord_restart_enabled(value: bool) -> bool:
    return _set_bool(("program", "discord_auto_restart"), value)


def get_max_blocked() -> bool:
    return _get_bool(("program", "max_blocked"), False)


def set_max_blocked(value: bool) -> bool:
    return _set_bool(("program", "max_blocked"), value)


def get_russian_state_media_blocked() -> bool:
    return _get_bool(("program", "russian_state_media_blocked"), False)


def set_russian_state_media_blocked(value: bool) -> bool:
    return _set_bool(("program", "russian_state_media_blocked"), value)


def set_defender_disabled_memory(value: bool) -> bool:
    return _set_bool(("program", "defender_disabled"), value)


def get_defender_disabled_memory() -> bool:
    return _get_bool(("program", "defender_disabled"), False)


def get_window_geometry() -> dict[str, Any]:
    # Пять полей читаются под одним локом: геометрия запрашивается на каждом
    # изменении размера окна.
    with _SETTINGS_LOCK:
        data = _read_settings_cached_locked()
        return {
            "x": _get_path_value(data, ("window", "x"), None),
            "y": _get_path_value(data, ("window", "y"), None),
            "width": _get_path_value(data, ("window", "width"), None),
            "height": _get_path_value(data, ("window", "height"), None),
            "maximized": bool(_get_path_value(data, ("window", "maximized"), False)),
        }


def set_window_geometry(*, x: int | None, y: int | None, width: int | None, height: int | None, maximized: bool) -> bool:
    _update_settings(
        lambda data: data["window"].update(
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "maximized": bool(maximized),
            }
        )
    )
    return True


def get_window_opacity() -> int:
    return _get_int(("window", "opacity"), _DEFAULT_WINDOW_OPACITY)


def set_window_opacity(value: int) -> bool:
    return _set_int(("window", "opacity"), value)


def get_tray_close_mode() -> str:
    return _get_str_in(("window", "tray_close_mode"), _VALID_TRAY_CLOSE_MODES, _TRAY_CLOSE_MODE_NORMAL)


def set_tray_close_mode(value: str) -> bool:
    return _set_str_in(("window", "tray_close_mode"), value, _VALID_TRAY_CLOSE_MODES, _TRAY_CLOSE_MODE_NORMAL)


def get_display_mode() -> str:
    return _get_str(("appearance", "display_mode"), "dark")


def set_display_mode(value: str) -> bool:
    return _set_str(("appearance", "display_mode"), value)


def get_ui_language() -> str:
    return _get_str(("appearance", "ui_language"), "ru")


def set_ui_language(value: str) -> bool:
    return _set_str(("appearance", "ui_language"), value)


def get_mica_enabled() -> bool:
    return _get_bool(("appearance", "mica_enabled"), True)


def set_mica_enabled(value: bool) -> bool:
    return _set_bool(("appearance", "mica_enabled"), value)


def get_background_preset() -> str:
    return _get_str(("appearance", "background_preset"), "standard")


def set_background_preset(value: str) -> bool:
    return _set_str(("appearance", "background_preset"), value)


def get_rkn_background() -> str | None:
    return _get_nullable_str(("appearance", "rkn_background"))


def set_rkn_background(value: str | None) -> bool:
    return _set_nullable_str(("appearance", "rkn_background"), value)


def get_animations_enabled() -> bool:
    return _get_bool(("appearance", "animations_enabled"), False)


def set_animations_enabled(value: bool) -> bool:
    return _set_bool(("appearance", "animations_enabled"), value)


def get_smooth_scroll_enabled() -> bool:
    return _get_bool(("appearance", "smooth_scroll_enabled"), False)


def set_smooth_scroll_enabled(value: bool) -> bool:
    return _set_bool(("appearance", "smooth_scroll_enabled"), value)


def get_editor_smooth_scroll_enabled() -> bool:
    return _get_bool(("appearance", "editor_smooth_scroll_enabled"), False)


def set_editor_smooth_scroll_enabled(value: bool) -> bool:
    return _set_bool(("appearance", "editor_smooth_scroll_enabled"), value)


def get_sidebar_icon_style() -> str:
    return _get_str(("appearance", "sidebar_icon_style"), "standard")


def set_sidebar_icon_style(value: str) -> bool:
    return _set_str(("appearance", "sidebar_icon_style"), value)


def get_accent_color() -> str | None:
    return _get_nullable_str(("appearance", "accent_color"))


def set_accent_color(value: str | None) -> bool:
    return _set_nullable_str(("appearance", "accent_color"), value)


def get_follow_windows_accent() -> bool:
    return _get_bool(("appearance", "follow_windows_accent"), False)


def set_follow_windows_accent(value: bool) -> bool:
    return _set_bool(("appearance", "follow_windows_accent"), value)


def get_tinted_background() -> bool:
    return _get_bool(("appearance", "tinted_background"), False)


def set_tinted_background(value: bool) -> bool:
    return _set_bool(("appearance", "tinted_background"), value)


def get_tinted_background_intensity() -> int:
    return _get_int(("appearance", "tinted_background_intensity"), _DEFAULT_TINTED_INTENSITY)


def set_tinted_background_intensity(value: int) -> bool:
    return _set_int(("appearance", "tinted_background_intensity"), value)


def get_garland_enabled() -> bool:
    return _get_bool(("appearance", "garland_enabled"), False)


def set_garland_enabled(value: bool) -> bool:
    return _set_bool(("appearance", "garland_enabled"), value)


def get_snowflakes_enabled() -> bool:
    return _get_bool(("appearance", "snowflakes_enabled"), False)


def set_snowflakes_enabled(value: bool) -> bool:
    return _set_bool(("appearance", "snowflakes_enabled"), value)


def get_selected_theme() -> str:
    return _get_str(("appearance", "selected_theme"), "")


def set_selected_theme(value: str) -> bool:
    return _set_str(("appearance", "selected_theme"), value)


def get_windows_system_accent() -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Accent",
            0,
            winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AccentColorMenu")
            r = value & 0xFF
            g = (value >> 8) & 0xFF
            b = (value >> 16) & 0xFF
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None


def get_tray_hint_shown() -> bool:
    return _get_bool(("warnings", "tray_hint_shown"), False)


def set_tray_hint_shown(value: bool = True) -> bool:
    return _set_bool(("warnings", "tray_hint_shown"), value)


def get_telega_warning_disabled() -> bool:
    return _get_bool(("warnings", "disable_telega_warning"), False)


def set_telega_warning_disabled(value: bool) -> bool:
    return _set_bool(("warnings", "disable_telega_warning"), value)


def get_kaspersky_warning_disabled() -> bool:
    return _get_bool(("warnings", "disable_kaspersky_warning"), False)


def set_kaspersky_warning_disabled(value: bool) -> bool:
    return _set_bool(("warnings", "disable_kaspersky_warning"), value)


def get_isp_dns_info_shown() -> bool:
    return _get_bool(("warnings", "isp_dns_info_shown"), False)


def set_isp_dns_info_shown(value: bool) -> bool:
    return _set_bool(("warnings", "isp_dns_info_shown"), value)


def get_tg_proxy_deeplink_done() -> bool:
    return _get_bool(("warnings", "tg_proxy_deeplink_done"), False)


def set_tg_proxy_deeplink_done(value: bool) -> bool:
    return _set_bool(("warnings", "tg_proxy_deeplink_done"), value)


def get_force_dns_enabled() -> bool:
    return _get_bool(("dns", "force_dns_enabled"), False)


def set_force_dns_enabled(value: bool) -> bool:
    return _set_bool(("dns", "force_dns_enabled"), value)


def get_dns_crash_count() -> int:
    return _get_int(("dns", "dns_crash_count"), 0)


def set_dns_crash_count(value: int) -> bool:
    return _set_int(("dns", "dns_crash_count"), value)


def get_custom_dns_servers() -> list[dict[str, Any]]:
    value = _read_path_value(("dns", "custom_servers"), [])
    return value if isinstance(value, list) else []


def set_custom_dns_servers(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = _update_settings(lambda data: _set_path_value(data, ("dns", "custom_servers"), value))
    return copy.deepcopy(updated["dns"]["custom_servers"])


def increment_dns_crash_count() -> int:
    updated = _update_settings(
        lambda data: _set_path_value(
            data,
            ("dns", "dns_crash_count"),
            _as_int(_get_path_value(data, ("dns", "dns_crash_count"), 0), 0, minimum=0) + 1,
        )
    )
    return int(updated["dns"]["dns_crash_count"])


def reset_dns_crash_count() -> bool:
    return _set_int(("dns", "dns_crash_count"), 0)


def get_active_hosts_domains() -> set[str]:
    items = _read_path_value(("hosts", "active_domains"), [])
    if not isinstance(items, list):
        return set()
    return set(_unique_str_list(items))


def set_active_hosts_domains(domains: set[str] | list[str]) -> bool:
    normalized = _unique_str_list(list(domains) if isinstance(domains, set) else domains)
    _update_settings(lambda data: _set_path_value(data, ("hosts", "active_domains"), normalized))
    return True


def add_active_hosts_domain(domain: str) -> bool:
    domains = get_active_hosts_domains()
    item = _as_clean_str(domain)
    if item:
        domains.add(item)
    return set_active_hosts_domains(domains)


def remove_active_hosts_domain(domain: str) -> bool:
    domains = get_active_hosts_domains()
    domains.discard(_as_clean_str(domain))
    return set_active_hosts_domains(domains)


def clear_active_hosts_domains() -> bool:
    return set_active_hosts_domains(set())


def get_hosts_selection() -> dict[str, str]:
    data = _read_path_value(("hosts", "selection"), {})
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for raw_service_name, raw_profile_name in data.items():
        service_name = _as_clean_str(raw_service_name)
        profile_name = _as_clean_str(raw_profile_name)
        if service_name and profile_name:
            out[service_name] = profile_name
    return out


def set_hosts_selection(selection: dict[str, str]) -> bool:
    normalized: dict[str, str] = {}
    for raw_service_name, raw_profile_name in _as_dict(selection).items():
        service_name = _as_clean_str(raw_service_name)
        profile_name = _as_clean_str(raw_profile_name)
        if service_name and profile_name:
            normalized[service_name] = profile_name
    _update_settings(lambda data: _set_path_value(data, ("hosts", "selection"), normalized))
    return True


def get_tg_proxy_enabled() -> bool:
    return _get_bool(("telegram_proxy", "enabled"), True)


def set_tg_proxy_enabled(value: bool) -> bool:
    return _set_bool(("telegram_proxy", "enabled"), value)


def get_tg_proxy_host() -> str:
    return _get_str(("telegram_proxy", "host"), _DEFAULT_TG_PROXY_HOST)


def set_tg_proxy_host(value: str) -> bool:
    return _set_str(("telegram_proxy", "host"), value)


def get_tg_proxy_port() -> int:
    return _get_int(("telegram_proxy", "port"), _DEFAULT_TG_PROXY_PORT)


def set_tg_proxy_port(value: int) -> bool:
    return _set_int(("telegram_proxy", "port"), value)


def get_tg_proxy_mode() -> str:
    return _get_str(("telegram_proxy", "mode"), "mtproxy")


def set_tg_proxy_mode(value: str) -> bool:
    return _set_str(("telegram_proxy", "mode"), value)


def get_tg_proxy_upstream_enabled() -> bool:
    return _get_bool(("telegram_proxy", "upstream_enabled"), False)


def set_tg_proxy_upstream_enabled(value: bool) -> bool:
    return _set_bool(("telegram_proxy", "upstream_enabled"), value)


def get_tg_proxy_upstream_host() -> str:
    return _get_str(("telegram_proxy", "upstream_host"), "")


def set_tg_proxy_upstream_host(value: str) -> bool:
    return _set_str(("telegram_proxy", "upstream_host"), value)


def get_tg_proxy_upstream_port() -> int:
    return _get_int(("telegram_proxy", "upstream_port"), _DEFAULT_TG_PROXY_UPSTREAM_PORT)


def set_tg_proxy_upstream_port(value: int) -> bool:
    return _set_int(("telegram_proxy", "upstream_port"), value)


def get_tg_proxy_upstream_preset_id() -> str:
    return _get_str(("telegram_proxy", "upstream_preset_id"), "")


def set_tg_proxy_upstream_preset_id(value: str) -> bool:
    return _set_str(("telegram_proxy", "upstream_preset_id"), value)


def get_tg_proxy_upstream_mode() -> str:
    return _get_str(("telegram_proxy", "upstream_mode"), "fallback")


def set_tg_proxy_upstream_mode(value: str) -> bool:
    return _set_str(("telegram_proxy", "upstream_mode"), value)


def get_tg_proxy_upstream_udp_enabled() -> bool:
    return _get_bool(("telegram_proxy", "upstream_udp_enabled"), False)


def set_tg_proxy_upstream_udp_enabled(value: bool) -> bool:
    return _set_bool(("telegram_proxy", "upstream_udp_enabled"), value)


def get_tg_proxy_upstream_user() -> str:
    return _get_str(("telegram_proxy", "upstream_user"), "")


def set_tg_proxy_upstream_user(value: str) -> bool:
    return _set_str(("telegram_proxy", "upstream_user"), value)


def get_tg_proxy_upstream_pass() -> str:
    return _get_str(("telegram_proxy", "upstream_pass"), "")


def set_tg_proxy_upstream_pass(value: str) -> bool:
    return _set_str(("telegram_proxy", "upstream_pass"), value)


def get_tg_proxy_cloudflare_enabled() -> bool:
    return _get_bool(("telegram_proxy", "cloudflare_enabled"), False)


def set_tg_proxy_cloudflare_enabled(value: bool) -> bool:
    return _set_bool(("telegram_proxy", "cloudflare_enabled"), value)


def get_tg_proxy_cloudflare_domains() -> list[str]:
    return _get_str_list(("telegram_proxy", "cloudflare_domains"))


def set_tg_proxy_cloudflare_domains(value: object) -> bool:
    return _set_str_list(("telegram_proxy", "cloudflare_domains"), value)


def get_tg_proxy_cloudflare_worker_enabled() -> bool:
    return _get_bool(("telegram_proxy", "cloudflare_worker_enabled"), False)


def set_tg_proxy_cloudflare_worker_enabled(value: bool) -> bool:
    return _set_bool(("telegram_proxy", "cloudflare_worker_enabled"), value)


def get_tg_proxy_cloudflare_worker_domains() -> list[str]:
    return _get_str_list(("telegram_proxy", "cloudflare_worker_domains"))


def set_tg_proxy_cloudflare_worker_domains(value: object) -> bool:
    return _set_str_list(("telegram_proxy", "cloudflare_worker_domains"), value)


def get_tg_proxy_mtproxy_secret() -> str:
    return _get_str(("telegram_proxy", "mtproxy_secret"), "")


def set_tg_proxy_mtproxy_secret(value: str) -> bool:
    return _set_str(("telegram_proxy", "mtproxy_secret"), _normalize_hex_secret(value))


def get_tg_proxy_dc_ip() -> list[str]:
    return _get_str_list(("telegram_proxy", "dc_ip"))


def set_tg_proxy_dc_ip(value: object) -> bool:
    return _set_dc_ip_list(("telegram_proxy", "dc_ip"), value)


def get_tg_proxy_pool_size() -> int:
    return _get_int(("telegram_proxy", "pool_size"), 4)


def set_tg_proxy_pool_size(value: int) -> bool:
    return _set_int(("telegram_proxy", "pool_size"), value)


def get_tg_proxy_buffer_kb() -> int:
    return _get_int(("telegram_proxy", "buffer_kb"), 256)


def set_tg_proxy_buffer_kb(value: int) -> bool:
    return _set_int(("telegram_proxy", "buffer_kb"), value)


def get_tg_proxy_fake_tls_domain() -> str:
    return _get_str(("telegram_proxy", "fake_tls_domain"), "")


def set_tg_proxy_fake_tls_domain(value: str) -> bool:
    return _set_str(("telegram_proxy", "fake_tls_domain"), value)


def get_tg_proxy_proxy_protocol() -> bool:
    return _get_bool(("telegram_proxy", "proxy_protocol"), False)


def set_tg_proxy_proxy_protocol(value: bool) -> bool:
    return _set_bool(("telegram_proxy", "proxy_protocol"), value)


def get_orchestra_strict_detection() -> bool:
    return _get_bool(("orchestra", "settings", "strict_detection"), True)


def set_orchestra_strict_detection(value: bool) -> bool:
    return _set_bool(("orchestra", "settings", "strict_detection"), value)


def get_orchestra_keep_debug_file() -> bool:
    return _get_bool(("orchestra", "settings", "keep_debug_file"), False)


def set_orchestra_keep_debug_file(value: bool) -> bool:
    return _set_bool(("orchestra", "settings", "keep_debug_file"), value)


def get_orchestra_auto_restart_on_discord_fail() -> bool:
    return _get_bool(("orchestra", "settings", "auto_restart_on_discord_fail"), True)


def set_orchestra_auto_restart_on_discord_fail(value: bool) -> bool:
    return _set_bool(("orchestra", "settings", "auto_restart_on_discord_fail"), value)


def get_orchestra_discord_fails_for_restart() -> int:
    return _get_int(("orchestra", "settings", "discord_fails_for_restart"), 3)


def set_orchestra_discord_fails_for_restart(value: int) -> bool:
    return _set_int(("orchestra", "settings", "discord_fails_for_restart"), value)


def get_orchestra_lock_successes() -> int:
    return _get_int(("orchestra", "settings", "lock_successes"), 3)


def set_orchestra_lock_successes(value: int) -> bool:
    return _set_int(("orchestra", "settings", "lock_successes"), value)


def get_orchestra_unlock_fails() -> int:
    return _get_int(("orchestra", "settings", "unlock_fails"), 3)


def set_orchestra_unlock_fails(value: int) -> bool:
    return _set_int(("orchestra", "settings", "unlock_fails"), value)


def get_orchestra_whitelist_user_domains() -> list[str]:
    values = _read_path_value(("orchestra", "whitelist", "user_domains"), [])
    return _unique_str_list(values)


def set_orchestra_whitelist_user_domains(domains: list[str]) -> bool:
    normalized = [_normalize_lookup_key(item) for item in _unique_str_list(domains) if _normalize_lookup_key(item)]
    _update_settings(lambda data: _set_path_value(data, ("orchestra", "whitelist", "user_domains"), normalized))
    return True


def add_orchestra_whitelist_domain(domain: str) -> bool:
    items = get_orchestra_whitelist_user_domains()
    value = _normalize_lookup_key(domain)
    if value and value not in items:
        items.append(value)
    return set_orchestra_whitelist_user_domains(items)


def remove_orchestra_whitelist_domain(domain: str) -> bool:
    value = _normalize_lookup_key(domain)
    items = [item for item in get_orchestra_whitelist_user_domains() if item != value]
    return set_orchestra_whitelist_user_domains(items)


def clear_orchestra_whitelist_user_domains() -> bool:
    return set_orchestra_whitelist_user_domains([])


def get_orchestra_locked_map(askey: str) -> dict[str, int]:
    key = _normalize_askey(askey)
    data = _read_path_value(("orchestra", "locked", key), {})
    return data if isinstance(data, dict) else {}


def set_orchestra_locked_map(askey: str, data: dict[str, int]) -> bool:
    key = _normalize_askey(askey)
    _update_settings(lambda settings: _set_path_value(settings, ("orchestra", "locked", key), _as_dict(data)))
    return True


def set_orchestra_locked_strategy(askey: str, target: str, strategy: int) -> bool:
    key = _normalize_askey(askey)
    lookup_key = _normalize_lookup_key(target)
    if not lookup_key:
        return False

    def _mutator(data: dict[str, Any]) -> None:
        mapping = _as_dict(_get_path_value(data, ("orchestra", "locked", key), {}))
        mapping[lookup_key] = int(strategy)
        _set_path_value(data, ("orchestra", "locked", key), mapping)

    _update_settings(_mutator)
    return True


def remove_orchestra_locked_target(askey: str, target: str) -> bool:
    key = _normalize_askey(askey)
    lookup_key = _normalize_lookup_key(target)

    def _mutator(data: dict[str, Any]) -> None:
        mapping = _as_dict(_get_path_value(data, ("orchestra", "locked", key), {}))
        mapping.pop(lookup_key, None)
        _set_path_value(data, ("orchestra", "locked", key), mapping)

    _update_settings(_mutator)
    return True


def clear_orchestra_locked_map(askey: str) -> bool:
    return set_orchestra_locked_map(askey, {})


def get_orchestra_user_locked(askey: str) -> list[str]:
    key = _normalize_askey(askey)
    values = _read_path_value(("orchestra", "user_locked", key), [])
    return [_normalize_lookup_key(item) for item in _unique_str_list(values) if _normalize_lookup_key(item)]


def set_orchestra_user_locked(askey: str, values: list[str]) -> bool:
    key = _normalize_askey(askey)
    normalized = [_normalize_lookup_key(item) for item in _unique_str_list(values) if _normalize_lookup_key(item)]
    _update_settings(lambda data: _set_path_value(data, ("orchestra", "user_locked", key), normalized))
    return True


def add_orchestra_user_locked(askey: str, target: str) -> bool:
    items = get_orchestra_user_locked(askey)
    value = _normalize_lookup_key(target)
    if value and value not in items:
        items.append(value)
    return set_orchestra_user_locked(askey, items)


def remove_orchestra_user_locked(askey: str, target: str) -> bool:
    value = _normalize_lookup_key(target)
    items = [item for item in get_orchestra_user_locked(askey) if item != value]
    return set_orchestra_user_locked(askey, items)


def clear_orchestra_user_locked(askey: str) -> bool:
    return set_orchestra_user_locked(askey, [])


def get_orchestra_user_blocked(askey: str) -> dict[str, list[int]]:
    key = _normalize_askey(askey)
    data = _read_path_value(("orchestra", "user_blocked", key), {})
    return data if isinstance(data, dict) else {}


def set_orchestra_user_blocked(askey: str, data: dict[str, list[int]]) -> bool:
    key = _normalize_askey(askey)
    _update_settings(lambda settings: _set_path_value(settings, ("orchestra", "user_blocked", key), _as_dict(data)))
    return True


def set_orchestra_user_blocked_strategies(askey: str, target: str, strategies: list[int]) -> bool:
    key = _normalize_askey(askey)
    lookup_key = _normalize_lookup_key(target)
    if not lookup_key:
        return False

    def _mutator(data: dict[str, Any]) -> None:
        mapping = _as_dict(_get_path_value(data, ("orchestra", "user_blocked", key), {}))
        normalized = _unique_int_list(strategies)
        if normalized:
            mapping[lookup_key] = normalized
        else:
            mapping.pop(lookup_key, None)
        _set_path_value(data, ("orchestra", "user_blocked", key), mapping)

    _update_settings(_mutator)
    return True


def remove_orchestra_user_blocked_target(askey: str, target: str) -> bool:
    key = _normalize_askey(askey)
    lookup_key = _normalize_lookup_key(target)

    def _mutator(data: dict[str, Any]) -> None:
        mapping = _as_dict(_get_path_value(data, ("orchestra", "user_blocked", key), {}))
        mapping.pop(lookup_key, None)
        _set_path_value(data, ("orchestra", "user_blocked", key), mapping)

    _update_settings(_mutator)
    return True


def clear_orchestra_user_blocked(askey: str) -> bool:
    return set_orchestra_user_blocked(askey, {})


def get_orchestra_history() -> dict[str, Any]:
    data = _read_path_value(("orchestra", "history"), {})
    return data if isinstance(data, dict) else {}


def set_orchestra_history(data: dict[str, Any]) -> bool:
    _update_settings(lambda settings: _set_path_value(settings, ("orchestra", "history"), _as_dict(data)))
    return True


def get_orchestra_history_for_target(target: str) -> dict[str, Any]:
    lookup_key = _normalize_lookup_key(target)
    return copy.deepcopy(get_orchestra_history().get(lookup_key, {}))


def set_orchestra_history_for_target(target: str, data: dict[str, Any]) -> bool:
    lookup_key = _normalize_lookup_key(target)
    if not lookup_key:
        return False

    def _mutator(settings: dict[str, Any]) -> None:
        history = _as_dict(_get_path_value(settings, ("orchestra", "history"), {}))
        history[lookup_key] = _as_dict(data)
        _set_path_value(settings, ("orchestra", "history"), history)

    _update_settings(_mutator)
    return True


def remove_orchestra_history_target(target: str) -> bool:
    lookup_key = _normalize_lookup_key(target)

    def _mutator(settings: dict[str, Any]) -> None:
        history = _as_dict(_get_path_value(settings, ("orchestra", "history"), {}))
        history.pop(lookup_key, None)
        _set_path_value(settings, ("orchestra", "history"), history)

    _update_settings(_mutator)
    return True


def clear_orchestra_history() -> bool:
    return set_orchestra_history({})


__all__ = [
    "append_self_repair_attempt",
    "close_settings_database",
    "get_accent_color",
    "get_active_hosts_domains",
    "get_animations_enabled",
    "get_auto_update_enabled",
    "get_background_preset",
    "get_discord_restart_enabled",
    "get_display_mode",
    "get_dns_crash_count",
    "get_custom_dns_servers",
    "get_dpi_autostart",
    "get_editor_smooth_scroll_enabled",
    "get_defender_disabled_memory",
    "get_follow_windows_accent",
    "get_force_dns_enabled",
    "get_folders_settings",
    "get_garland_enabled",
    "get_gui_autostart_enabled",
    "get_hosts_selection",
    "get_isp_dns_info_shown",
    "get_kaspersky_warning_disabled",
    "get_last_seen_version",
    "get_max_blocked",
    "get_mica_enabled",
    "get_orchestra_auto_restart_on_discord_fail",
    "get_orchestra_discord_fails_for_restart",
    "get_orchestra_history",
    "get_orchestra_history_for_target",
    "get_orchestra_keep_debug_file",
    "get_orchestra_lock_successes",
    "get_orchestra_locked_map",
    "get_orchestra_settings",
    "get_orchestra_strict_detection",
    "get_orchestra_unlock_fails",
    "get_orchestra_user_blocked",
    "get_orchestra_user_locked",
    "get_orchestra_whitelist_user_domains",
    "get_program_settings",
    "get_profile_strategy_state_settings",
    "get_rkn_background",
    "get_russian_state_media_blocked",
    "get_selected_theme",
    "get_selected_source_preset_file_name",
    "get_self_repair_attempts",
    "get_sidebar_icon_style",
    "get_settings_database_path",
    "get_settings_revision",
    "get_smooth_scroll_enabled",
    "get_snowflakes_enabled",
    "get_strategy_launch_method",
    "get_telega_warning_disabled",
    "get_tg_proxy_deeplink_done",
    "get_tg_proxy_cloudflare_domains",
    "get_tg_proxy_cloudflare_enabled",
    "get_tg_proxy_cloudflare_worker_domains",
    "get_tg_proxy_cloudflare_worker_enabled",
    "get_tg_proxy_buffer_kb",
    "get_tg_proxy_dc_ip",
    "get_tg_proxy_enabled",
    "get_tg_proxy_fake_tls_domain",
    "get_tg_proxy_host",
    "get_tg_proxy_mode",
    "get_tg_proxy_mtproxy_secret",
    "get_tg_proxy_pool_size",
    "get_tg_proxy_port",
    "get_tg_proxy_proxy_protocol",
    "get_tg_proxy_upstream_enabled",
    "get_tg_proxy_upstream_host",
    "get_tg_proxy_upstream_mode",
    "get_tg_proxy_upstream_udp_enabled",
    "get_tg_proxy_upstream_pass",
    "get_tg_proxy_upstream_preset_id",
    "get_tg_proxy_upstream_port",
    "get_tg_proxy_upstream_user",
    "get_tinted_background",
    "get_tinted_background_intensity",
    "get_tray_close_mode",
    "get_tray_hint_shown",
    "get_ui_language",
    "get_window_geometry",
    "get_window_opacity",
    "get_windows_system_accent",
    "increment_dns_crash_count",
    "prepare_settings_database",
    "read_settings",
    "remove_active_hosts_domain",
    "remove_orchestra_history_target",
    "remove_orchestra_locked_target",
    "remove_orchestra_user_blocked_target",
    "remove_orchestra_user_locked",
    "remove_orchestra_whitelist_domain",
    "reset_dns_crash_count",
    "reset_self_repair_attempts",
    "reset_settings",
    "set_accent_color",
    "set_active_hosts_domains",
    "set_animations_enabled",
    "set_auto_update_enabled",
    "set_background_preset",
    "set_defender_disabled_memory",
    "set_discord_restart_enabled",
    "set_display_mode",
    "set_dns_crash_count",
    "set_custom_dns_servers",
    "set_dpi_autostart",
    "set_editor_smooth_scroll_enabled",
    "set_follow_windows_accent",
    "set_force_dns_enabled",
    "set_folders_settings",
    "set_garland_enabled",
    "set_gui_autostart_enabled",
    "set_hosts_selection",
    "set_isp_dns_info_shown",
    "set_kaspersky_warning_disabled",
    "set_last_seen_version",
    "set_max_blocked",
    "set_mica_enabled",
    "set_orchestra_auto_restart_on_discord_fail",
    "set_orchestra_discord_fails_for_restart",
    "set_orchestra_history",
    "set_orchestra_history_for_target",
    "set_orchestra_keep_debug_file",
    "set_orchestra_lock_successes",
    "set_orchestra_locked_map",
    "set_orchestra_locked_strategy",
    "set_orchestra_settings",
    "set_orchestra_strict_detection",
    "set_orchestra_unlock_fails",
    "set_orchestra_user_blocked",
    "set_orchestra_user_blocked_strategies",
    "set_orchestra_user_locked",
    "set_orchestra_whitelist_user_domains",
    "set_program_settings",
    "set_profile_strategy_state_settings",
    "set_rkn_background",
    "set_russian_state_media_blocked",
    "set_selected_theme",
    "set_selected_source_preset_file_name",
    "set_sidebar_icon_style",
    "set_smooth_scroll_enabled",
    "set_snowflakes_enabled",
    "set_strategy_launch_method",
    "set_telega_warning_disabled",
    "set_tg_proxy_deeplink_done",
    "set_tg_proxy_cloudflare_domains",
    "set_tg_proxy_cloudflare_enabled",
    "set_tg_proxy_cloudflare_worker_domains",
    "set_tg_proxy_cloudflare_worker_enabled",
    "set_tg_proxy_buffer_kb",
    "set_tg_proxy_dc_ip",
    "set_tg_proxy_enabled",
    "set_tg_proxy_fake_tls_domain",
    "set_tg_proxy_host",
    "set_tg_proxy_mode",
    "set_tg_proxy_mtproxy_secret",
    "set_tg_proxy_pool_size",
    "set_tg_proxy_port",
    "set_tg_proxy_proxy_protocol",
    "set_tg_proxy_upstream_enabled",
    "set_tg_proxy_upstream_host",
    "set_tg_proxy_upstream_mode",
    "set_tg_proxy_upstream_udp_enabled",
    "set_tg_proxy_upstream_pass",
    "set_tg_proxy_upstream_preset_id",
    "set_tg_proxy_upstream_port",
    "set_tg_proxy_upstream_user",
    "set_tinted_background",
    "set_tinted_background_intensity",
    "set_tray_close_mode",
    "set_tray_hint_shown",
    "set_ui_language",
    "set_window_geometry",
    "set_window_opacity",
    "clear_selected_source_preset_file_name",
]
