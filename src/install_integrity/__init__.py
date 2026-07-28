from __future__ import annotations

"""Целостность установленной поставки: манифест, проверка, объяснение.

Модуль не знает ни про Qt, ни про обновление, ни про запуск DPI. Он только
отвечает на вопрос «соответствует ли то, что лежит на диске, тому, что
поставила эта версия», и объясняет расхождение человеческим языком.
"""

from .manifest import (
    CRITICAL_GROUPS,
    MANIFEST_SCOPES,
    build_manifest,
    dump_manifest,
    load_manifest,
    manifest_path,
    missing_critical_groups,
    write_manifest,
)
from .messages import IntegrityMessage, describe_report
from .models import (
    InstallManifest,
    IntegrityCause,
    IntegrityFinding,
    IntegrityReport,
    MANIFEST_FILE_NAME,
    MANIFEST_SCHEMA_VERSION,
    ManifestEntry,
    ROLE_CRITICAL,
    ROLE_REQUIRED,
)
from .verify import verify_fast, verify_full, verify_installation

__all__ = [
    "CRITICAL_GROUPS",
    "InstallManifest",
    "IntegrityCause",
    "IntegrityFinding",
    "IntegrityMessage",
    "IntegrityReport",
    "MANIFEST_FILE_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_SCOPES",
    "ManifestEntry",
    "ROLE_CRITICAL",
    "ROLE_REQUIRED",
    "build_manifest",
    "describe_report",
    "dump_manifest",
    "load_manifest",
    "manifest_path",
    "missing_critical_groups",
    "verify_fast",
    "verify_full",
    "verify_installation",
    "write_manifest",
]
