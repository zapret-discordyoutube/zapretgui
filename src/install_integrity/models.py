from __future__ import annotations

"""Доменная модель целостности установленной поставки.

Манифест описывает, какие файлы обязана содержать установка конкретной
версии. Он генерируется сборкой и кладётся в ``_internal``, который
установщик заменяет целиком, поэтому манифест всегда соответствует
установленной версии.
"""

from dataclasses import dataclass
from enum import Enum


MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILE_NAME = "install_manifest.json"

ROLE_CRITICAL = "critical"
ROLE_REQUIRED = "required"
ALL_ROLES = (ROLE_CRITICAL, ROLE_REQUIRED)

KIND_MISSING = "missing"
KIND_CORRUPTED = "corrupted"


class IntegrityCause(str, Enum):
    """Единственная причина, объясняющая состояние установки."""

    OK = "ok"
    # Манифеста нет: запуск из исходников или сборка без манифеста.
    # Проверять нечем, и это не повод мешать пользователю.
    MANIFEST_ABSENT = "manifest_absent"
    # Каталоги поставки на месте, отдельные файлы исчезли: файл удалили
    # уже после установки. На практике это почти всегда антивирус.
    REMOVED_AFTER_INSTALL = "removed_after_install"
    # Каталогов поставки нет: установка не завершилась или была повреждена.
    INCOMPLETE_INSTALL = "incomplete_install"
    # Файлы на месте, но их содержимое не совпадает с манифестом.
    CORRUPTED = "corrupted"


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """Один поставляемый файл.

    ``engines`` пустой — файл нужен любому методу запуска. Иначе перечислены
    движки, для которых файл обязателен: отсутствие winws.exe не должно
    блокировать запуск Zapret 2 и наоборот.
    """

    path: str
    size: int
    sha256: str
    role: str = ROLE_REQUIRED
    engines: tuple[str, ...] = ()

    @property
    def is_critical(self) -> bool:
        return self.role == ROLE_CRITICAL

    def required_for(self, engine: str | None) -> bool:
        if not self.engines:
            return True
        if not engine:
            return True
        return str(engine) in self.engines

    def to_json(self) -> dict:
        payload: dict = {
            "path": self.path,
            "size": int(self.size),
            "sha256": self.sha256,
            "role": self.role,
        }
        if self.engines:
            payload["engines"] = list(self.engines)
        return payload


@dataclass(frozen=True, slots=True)
class InstallManifest:
    schema: int
    version: str
    channel: str
    entries: tuple[ManifestEntry, ...]

    def critical_entries(self) -> tuple[ManifestEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_critical)

    def to_json(self) -> dict:
        return {
            "schema": int(self.schema),
            "version": self.version,
            "channel": self.channel,
            "files": [entry.to_json() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class IntegrityFinding:
    """Одна претензия к установке."""

    path: str
    kind: str
    role: str
    engines: tuple[str, ...] = ()

    @property
    def is_critical(self) -> bool:
        return self.role == ROLE_CRITICAL

    def blocks(self, engine: str | None) -> bool:
        if not self.is_critical:
            return False
        if not self.engines or not engine:
            return True
        return str(engine) in self.engines


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    cause: IntegrityCause
    findings: tuple[IntegrityFinding, ...] = ()
    manifest_version: str = ""
    checked_files: int = 0
    deep: bool = False

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def checked(self) -> bool:
        """Проверка реально состоялась (манифест был доступен)."""
        return self.cause is not IntegrityCause.MANIFEST_ABSENT

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.findings if f.kind == KIND_MISSING)

    @property
    def corrupted(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.findings if f.kind == KIND_CORRUPTED)

    def blocking_findings(self, engine: str | None = None) -> tuple[IntegrityFinding, ...]:
        return tuple(finding for finding in self.findings if finding.blocks(engine))

    def blocks_launch(self, engine: str | None = None) -> bool:
        return bool(self.blocking_findings(engine))


__all__ = [
    "ALL_ROLES",
    "InstallManifest",
    "IntegrityCause",
    "IntegrityFinding",
    "IntegrityReport",
    "KIND_CORRUPTED",
    "KIND_MISSING",
    "MANIFEST_FILE_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestEntry",
    "ROLE_CRITICAL",
    "ROLE_REQUIRED",
]
