"""Read-only repository for the shipped Hosts SQLite catalog.

The database is a ready application resource.  Runtime code never creates,
migrates or modifies it; user choices belong to settings/settings.sqlite3.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


CATALOG_APPLICATION_ID = 0x5A484354  # "ZHCT" -- Zapret Hosts Catalog
CATALOG_SCHEMA_VERSION = 1
CATALOG_FILE_NAME = "hosts_catalog.sqlite3"
HOSTS_PROFILE_ID = "hosts"
HOSTS_PROFILE_NAME = "Вкл. (активировать hosts)"


class HostsCatalogError(RuntimeError):
    """The shipped catalog is missing, damaged or has an unsupported schema."""


@dataclass(frozen=True)
class HostsCatalog:
    dns_profiles: list[str]
    dns_profile_names: dict[str, str]
    services: dict[str, dict[str, list[str]]]
    service_entries: dict[str, list[tuple[str, list[str]]]]
    service_order: list[str]
    service_modes: dict[str, str]
    service_categories: dict[str, str]
    service_icons: dict[str, tuple[str, str | None]]
    service_id_by_name: dict[str, str]
    service_name_by_id: dict[str, str]
    catalog_version: str
    content_sha256: str


def empty_catalog() -> HostsCatalog:
    return HostsCatalog(
        dns_profiles=[],
        dns_profile_names={},
        services={},
        service_entries={},
        service_order=[],
        service_modes={},
        service_categories={},
        service_icons={},
        service_id_by_name={},
        service_name_by_id={},
        catalog_version="",
        content_sha256="",
    )


def file_content_signature(path: Path) -> tuple[int, int]:
    """Return a content signature that ignores timestamp-only file changes."""
    digest = hashlib.sha256(path.read_bytes()).digest()
    return int.from_bytes(digest[:8], "big", signed=False), path.stat().st_size


def _connect_read_only(path: Path) -> sqlite3.Connection:
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except (OSError, sqlite3.Error) as exc:
        raise HostsCatalogError(f"не удалось открыть базу только для чтения: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _hash_query(
    digest: "hashlib._Hash",
    connection: sqlite3.Connection,
    table_name: str,
    query: str,
) -> None:
    digest.update(table_name.encode("ascii"))
    digest.update(b"\n")
    for row in connection.execute(query):
        payload = json.dumps(
            list(row),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)


def compute_content_sha256(connection: sqlite3.Connection) -> str:
    """Hash all logical catalog rows except self-referential metadata."""
    digest = hashlib.sha256()
    _hash_query(
        digest,
        connection,
        "services",
        """
        SELECT service_id, name, category, kind, sort_order, enabled,
               icon_name, icon_color
        FROM services
        ORDER BY service_id
        """,
    )
    _hash_query(
        digest,
        connection,
        "dns_profiles",
        """
        SELECT profile_id, name, sort_order, enabled
        FROM dns_profiles
        ORDER BY profile_id
        """,
    )
    _hash_query(
        digest,
        connection,
        "domains",
        """
        SELECT domain_id, service_id, hostname, sort_order
        FROM domains
        ORDER BY domain_id
        """,
    )
    _hash_query(
        digest,
        connection,
        "dns_answers",
        """
        SELECT domain_id, profile_id, ip_address, priority
        FROM dns_answers
        ORDER BY domain_id, profile_id, priority, ip_address
        """,
    )
    _hash_query(
        digest,
        connection,
        "hosts_entries",
        """
        SELECT entry_id, service_id, hostname, ip_address, priority
        FROM hosts_entries
        ORDER BY entry_id
        """,
    )
    return digest.hexdigest()


def _read_meta(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["key"]): str(row["value"])
        for row in connection.execute("SELECT key, value FROM catalog_meta")
    }


def _validate_database(connection: sqlite3.Connection) -> dict[str, str]:
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).casefold() != "ok":
        detail = str(quick_check[0]) if quick_check is not None else "нет результата"
        raise HostsCatalogError(f"PRAGMA quick_check: {detail}")

    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if application_id != CATALOG_APPLICATION_ID:
        raise HostsCatalogError(
            f"неверный application_id: {application_id}, ожидался {CATALOG_APPLICATION_ID}"
        )

    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if user_version != CATALOG_SCHEMA_VERSION:
        raise HostsCatalogError(
            f"неподдерживаемая схема {user_version}, ожидалась {CATALOG_SCHEMA_VERSION}"
        )

    try:
        meta = _read_meta(connection)
    except sqlite3.Error as exc:
        raise HostsCatalogError(f"не удалось прочитать catalog_meta: {exc}") from exc

    if meta.get("schema_version") != str(CATALOG_SCHEMA_VERSION):
        raise HostsCatalogError("catalog_meta.schema_version не соответствует PRAGMA user_version")
    if not meta.get("catalog_version"):
        raise HostsCatalogError("catalog_meta.catalog_version отсутствует")
    expected_hash = str(meta.get("content_sha256") or "").casefold()
    if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
        raise HostsCatalogError("catalog_meta.content_sha256 имеет неверный формат")

    try:
        actual_hash = compute_content_sha256(connection)
    except sqlite3.Error as exc:
        raise HostsCatalogError(f"структура таблиц каталога повреждена: {exc}") from exc
    if actual_hash != expected_hash:
        raise HostsCatalogError(
            "логическая контрольная сумма каталога не совпадает с catalog_meta.content_sha256"
        )
    return meta


def _append_entry(
    *,
    profiles: list[str],
    services: dict[str, dict[str, list[str]]],
    entries: dict[str, list[tuple[str, list[str]]]],
    service_name: str,
    hostname: str,
    values_by_profile: dict[str, str],
) -> None:
    row = [""] * len(profiles)
    for profile_id, ip_address in values_by_profile.items():
        try:
            index = profiles.index(profile_id)
        except ValueError:
            continue
        row[index] = ip_address
    if not any(row):
        return
    entries.setdefault(service_name, []).append((hostname, row))
    services.setdefault(service_name, {})[hostname] = list(row)


def _load_snapshot(connection: sqlite3.Connection, meta: dict[str, str]) -> HostsCatalog:
    profile_rows = list(
        connection.execute(
            """
            SELECT profile_id, name
            FROM dns_profiles
            WHERE enabled = 1
            ORDER BY sort_order, profile_id
            """
        )
    )
    profiles = [str(row["profile_id"]) for row in profile_rows]
    profile_names = {
        str(row["profile_id"]): str(row["name"])
        for row in profile_rows
    }

    service_rows = list(
        connection.execute(
            """
            SELECT service_id, name, category, kind, icon_name, icon_color
            FROM services
            WHERE enabled = 1
            ORDER BY sort_order, service_id
            """
        )
    )
    service_order = [str(row["name"]) for row in service_rows]
    services: dict[str, dict[str, list[str]]] = {name: {} for name in service_order}
    entries: dict[str, list[tuple[str, list[str]]]] = {name: [] for name in service_order}
    service_modes = {
        str(row["name"]).casefold(): str(row["kind"])
        for row in service_rows
    }
    service_categories = {
        str(row["name"]): str(row["category"])
        for row in service_rows
    }
    service_icons = {
        str(row["name"]): (
            str(row["icon_name"] or "fa5s.globe"),
            str(row["icon_color"]) if row["icon_color"] is not None else None,
        )
        for row in service_rows
    }
    service_id_by_name = {
        str(row["name"]): str(row["service_id"])
        for row in service_rows
    }
    service_name_by_id = {
        str(row["service_id"]): str(row["name"])
        for row in service_rows
    }

    dns_service_name_by_id = {
        str(row["service_id"]): str(row["name"])
        for row in service_rows
        if str(row["kind"]) == "dns"
    }
    answer_rows = connection.execute(
        """
        SELECT d.domain_id, d.service_id, d.hostname, d.sort_order,
               a.profile_id, a.ip_address, a.priority
        FROM domains AS d
        JOIN services AS s ON s.service_id = d.service_id
        JOIN dns_answers AS a ON a.domain_id = d.domain_id
        WHERE s.enabled = 1
        ORDER BY s.sort_order, d.sort_order, d.domain_id, a.priority, a.profile_id, a.ip_address
        """
    )
    domain_values: dict[tuple[int, str, str], dict[int, dict[str, str]]] = {}
    domain_order: list[tuple[int, str, str]] = []
    for row in answer_rows:
        key = (int(row["domain_id"]), str(row["service_id"]), str(row["hostname"]))
        if key not in domain_values:
            domain_values[key] = {}
            domain_order.append(key)
        domain_values[key].setdefault(int(row["priority"]), {})[
            str(row["profile_id"])
        ] = str(row["ip_address"])

    for key in domain_order:
        _domain_id, service_id, hostname = key
        service_name = dns_service_name_by_id.get(service_id)
        if not service_name:
            continue
        for priority in sorted(domain_values[key]):
            _append_entry(
                profiles=profiles,
                services=services,
                entries=entries,
                service_name=service_name,
                hostname=hostname,
                values_by_profile=domain_values[key][priority],
            )

    hosts_rows = list(
        connection.execute(
            """
            SELECT h.service_id, h.hostname, h.ip_address
            FROM hosts_entries AS h
            JOIN services AS s ON s.service_id = h.service_id
            WHERE s.enabled = 1
            ORDER BY s.sort_order, h.priority, h.entry_id
            """
        )
    )
    if hosts_rows:
        profiles.append(HOSTS_PROFILE_ID)
        profile_names[HOSTS_PROFILE_ID] = HOSTS_PROFILE_NAME
    for row in hosts_rows:
        service_name = service_name_by_id.get(str(row["service_id"]))
        if not service_name:
            continue
        _append_entry(
            profiles=profiles,
            services=services,
            entries=entries,
            service_name=service_name,
            hostname=str(row["hostname"]),
            values_by_profile={HOSTS_PROFILE_ID: str(row["ip_address"])},
        )

    return HostsCatalog(
        dns_profiles=profiles,
        dns_profile_names=profile_names,
        services=services,
        service_entries=entries,
        service_order=service_order,
        service_modes=service_modes,
        service_categories=service_categories,
        service_icons=service_icons,
        service_id_by_name=service_id_by_name,
        service_name_by_id=service_name_by_id,
        catalog_version=meta["catalog_version"],
        content_sha256=meta["content_sha256"],
    )


def load_catalog(path: Path) -> HostsCatalog:
    if not path.is_file():
        raise HostsCatalogError(f"файл не найден: {path}")
    connection = _connect_read_only(path)
    try:
        meta = _validate_database(connection)
        return _load_snapshot(connection, meta)
    except HostsCatalogError:
        raise
    except sqlite3.Error as exc:
        raise HostsCatalogError(f"ошибка чтения SQLite: {exc}") from exc
    finally:
        connection.close()


__all__ = [
    "CATALOG_APPLICATION_ID",
    "CATALOG_FILE_NAME",
    "CATALOG_SCHEMA_VERSION",
    "HOSTS_PROFILE_ID",
    "HOSTS_PROFILE_NAME",
    "HostsCatalog",
    "HostsCatalogError",
    "compute_content_sha256",
    "empty_catalog",
    "file_content_signature",
    "load_catalog",
]
