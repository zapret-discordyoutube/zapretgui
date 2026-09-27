"""Read-only repository for the shipped winws2 fakes registry (SQLite).

The database is a ready application resource, built once and committed to the
private repository (``resources/system/fakes_catalog.sqlite3``).  Runtime code
never creates, migrates or modifies it.  It only answers "which ``--blob=`` line
declares fake NAME"; the preset text stays the source of truth for what runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


CATALOG_APPLICATION_ID = 0x5A464354  # "ZFCT" -- Zapret Fakes Catalog
CATALOG_SCHEMA_VERSION = 1
CATALOG_FILE_NAME = "fakes_catalog.sqlite3"

SOURCE_KINDS: frozenset[str] = frozenset({"file", "hex"})
FAKE_KINDS: frozenset[str] = frozenset(
    {"tls", "quic", "http", "stun", "dht", "discord", "wireguard", "dtls", "zeros", "other"}
)
BIN_REFERENCE_PREFIX = "@bin/"

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEX_RE = re.compile(r"^(?:[0-9A-Fa-f]{2})+$")


class FakesCatalogError(RuntimeError):
    """The shipped fakes registry is missing, damaged or has an unsupported schema."""


@dataclass(frozen=True, slots=True)
class FakeEntry:
    """One registry row: blob name and the exact value after ``--blob=<name>:``."""

    name: str
    source_kind: str
    file_name: str | None
    hex_value: str | None
    kind: str
    sni: str | None
    description: str
    same_bytes_as: str | None

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name or ""):
            raise ValueError(f"недопустимое имя blob-а: {self.name!r}")
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"{self.name}: неизвестный source_kind {self.source_kind!r}")
        if self.kind not in FAKE_KINDS:
            raise ValueError(f"{self.name}: неизвестный kind {self.kind!r}")
        if self.source_kind == "file":
            if self.hex_value is not None or not self.file_name:
                raise ValueError(f"{self.name}: для source_kind=file нужен только file_name")
            if any(sep in self.file_name for sep in ("/", "\\")) or self.file_name in {".", ".."}:
                raise ValueError(f"{self.name}: file_name — имя внутри bin/, без пути")
        else:
            if self.file_name is not None or not self.hex_value:
                raise ValueError(f"{self.name}: для source_kind=hex нужен только hex_value")
            if not _HEX_RE.match(self.hex_value):
                raise ValueError(
                    f"{self.name}: hex_value — чётное число hex-цифр без префикса 0x"
                )

    def blob_value(self) -> str:
        """Text after ``--blob=<name>:`` — ``@bin/<file>`` or ``0x<hex>``."""
        if self.source_kind == "file":
            return f"{BIN_REFERENCE_PREFIX}{self.file_name}"
        return f"0x{self.hex_value}"

    def blob_line(self) -> str:
        return f"--blob={self.name}:{self.blob_value()}"


@dataclass(frozen=True, slots=True)
class FakesCatalog:
    """Immutable registry snapshot: ``entries`` is ordered by ``sort_order``."""

    entries: Mapping[str, FakeEntry]
    catalog_version: str = ""
    content_sha256: str = field(default="")

    def __post_init__(self) -> None:
        frozen = MappingProxyType(dict(self.entries))
        for key, entry in frozen.items():
            if key != entry.name:
                raise ValueError(f"ключ {key!r} не совпадает с именем записи {entry.name!r}")
        object.__setattr__(self, "entries", frozen)


def file_content_signature(path: Path) -> tuple[int, int]:
    """Content signature that ignores timestamp-only file changes."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).digest()
    return int.from_bytes(digest[:8], "big", signed=False), len(data)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    try:
        resolved = path.resolve()
        is_windows_unc = os.name == "nt" and str(resolved).startswith(("\\\\", "//"))
        if is_windows_unc:
            # SQLite URI treats the server name as a forbidden authority unless
            # its optional SQLITE_ALLOW_URI_AUTHORITY flag was compiled in.
            connection = sqlite3.connect(resolved, timeout=5.0)
        else:
            uri = resolved.as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    except (OSError, sqlite3.Error) as exc:
        raise FakesCatalogError(f"не удалось открыть базу только для чтения: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def compute_content_sha256(connection: sqlite3.Connection) -> str:
    """Hash all logical registry rows except self-referential metadata."""
    digest = hashlib.sha256()
    digest.update(b"fakes\n")
    for row in connection.execute(
        """
        SELECT name, source_kind, file_name, hex_value, kind, sni,
               description, same_bytes_as, sort_order
        FROM fakes
        ORDER BY name
        """
    ):
        payload = json.dumps(
            list(row),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _validate_database(connection: sqlite3.Connection) -> dict[str, str]:
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).casefold() != "ok":
        detail = str(quick_check[0]) if quick_check is not None else "нет результата"
        raise FakesCatalogError(f"PRAGMA quick_check: {detail}")

    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if application_id != CATALOG_APPLICATION_ID:
        raise FakesCatalogError(
            f"неверный application_id: {application_id}, ожидался {CATALOG_APPLICATION_ID}"
        )

    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if user_version != CATALOG_SCHEMA_VERSION:
        raise FakesCatalogError(
            f"неподдерживаемая схема {user_version}, ожидалась {CATALOG_SCHEMA_VERSION}"
        )

    try:
        meta = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM catalog_meta")
        }
    except sqlite3.Error as exc:
        raise FakesCatalogError(f"не удалось прочитать catalog_meta: {exc}") from exc

    if meta.get("schema_version") != str(CATALOG_SCHEMA_VERSION):
        raise FakesCatalogError("catalog_meta.schema_version не соответствует PRAGMA user_version")
    if not meta.get("catalog_version"):
        raise FakesCatalogError("catalog_meta.catalog_version отсутствует")
    expected_hash = str(meta.get("content_sha256") or "").casefold()
    if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
        raise FakesCatalogError("catalog_meta.content_sha256 имеет неверный формат")

    try:
        actual_hash = compute_content_sha256(connection)
    except sqlite3.Error as exc:
        raise FakesCatalogError(f"структура таблицы fakes повреждена: {exc}") from exc
    if actual_hash != expected_hash:
        raise FakesCatalogError(
            "логическая контрольная сумма реестра не совпадает с catalog_meta.content_sha256"
        )
    return meta


def _load_snapshot(connection: sqlite3.Connection, meta: dict[str, str]) -> FakesCatalog:
    entries: dict[str, FakeEntry] = {}
    for row in connection.execute(
        """
        SELECT name, source_kind, file_name, hex_value, kind, sni,
               description, same_bytes_as
        FROM fakes
        ORDER BY sort_order, name
        """
    ):
        try:
            entry = FakeEntry(
                name=str(row["name"]),
                source_kind=str(row["source_kind"]),
                file_name=row["file_name"],
                hex_value=row["hex_value"],
                kind=str(row["kind"]),
                sni=row["sni"],
                description=str(row["description"]),
                same_bytes_as=row["same_bytes_as"],
            )
        except ValueError as exc:
            raise FakesCatalogError(f"неверная запись реестра: {exc}") from exc
        entries[entry.name] = entry
    return FakesCatalog(
        entries=entries,
        catalog_version=meta["catalog_version"],
        content_sha256=meta["content_sha256"],
    )


def read_fakes_catalog(path: Path) -> FakesCatalog:
    """Open, validate and read the registry file without any caching."""
    path = Path(path)
    if not path.is_file():
        raise FakesCatalogError(f"файл не найден: {path}")
    connection = _connect_read_only(path)
    try:
        meta = _validate_database(connection)
        return _load_snapshot(connection, meta)
    except FakesCatalogError:
        raise
    except sqlite3.Error as exc:
        raise FakesCatalogError(f"ошибка чтения SQLite: {exc}") from exc
    finally:
        connection.close()


_CACHE_LOCK = threading.RLock()
_CACHE: tuple[Path, tuple[int, int], FakesCatalog] | None = None


def load_fakes_catalog(path: str | Path | None = None) -> FakesCatalog:
    """Validated registry snapshot, cached by path and file content.

    ``path`` defaults to the installed ``system/fakes_catalog.sqlite3``.
    A missing or damaged file raises ``FakesCatalogError``: an empty fallback
    would silently drop ``--blob=`` declarations and winws2 would fail later.
    Disk and SQLite work: call outside the GUI thread.
    """
    global _CACHE
    if path is None:
        from config.runtime_layout import APPLICATION_PATHS

        path = APPLICATION_PATHS.fakes_catalog_database
    resolved = Path(path).resolve()
    with _CACHE_LOCK:
        try:
            signature = file_content_signature(resolved)
        except OSError as exc:
            raise FakesCatalogError(f"файл не найден: {resolved}: {exc}") from exc
        if _CACHE is not None and _CACHE[0] == resolved and _CACHE[1] == signature:
            return _CACHE[2]
        catalog = read_fakes_catalog(resolved)
        _CACHE = (resolved, signature, catalog)
        return catalog


def invalidate_fakes_catalog_cache() -> None:
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


__all__ = [
    "BIN_REFERENCE_PREFIX",
    "CATALOG_APPLICATION_ID",
    "CATALOG_FILE_NAME",
    "CATALOG_SCHEMA_VERSION",
    "FAKE_KINDS",
    "SOURCE_KINDS",
    "FakeEntry",
    "FakesCatalog",
    "FakesCatalogError",
    "compute_content_sha256",
    "file_content_signature",
    "invalidate_fakes_catalog_cache",
    "load_fakes_catalog",
    "read_fakes_catalog",
]
