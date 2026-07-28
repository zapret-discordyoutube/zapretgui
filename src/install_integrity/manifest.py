from __future__ import annotations

"""Чтение и построение манифеста поставки.

Модуль намеренно не зависит ни от Qt, ни от рантайма приложения: тот же код
использует сборка, когда собирает манифест из подготовленного installer stage.
Один формат и одна реализация для обеих сторон.
"""

import json
import os
from pathlib import Path

from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2, EXE_NAME_WINWS1, EXE_NAME_WINWS2
from utils.file_digest import sha256_file

from .models import (
    InstallManifest,
    MANIFEST_FILE_NAME,
    MANIFEST_SCHEMA_VERSION,
    ManifestEntry,
    ROLE_CRITICAL,
    ROLE_REQUIRED,
)


# Охват манифеста — только то, без чего программа не работает. Пользовательские
# данные (lists/user, presets, settings) и файлы, которые приложение само
# перезаписывает, в манифест не входят: их расхождение с поставкой нормально.
MANIFEST_SCOPES = ("exe", "bin", "lua", "windivert.filter")

_WINDIVERT_DRIVER_NAMES = ("Monkey64.sys", "WinDivert64.sys")


class CriticalGroup:
    """Группа взаимозаменяемых файлов, без которых запуск невозможен.

    Драйвер WinDivert в поставке может называться по-разному, поэтому
    критичность задаётся группой кандидатов, а не одним путём.
    """

    __slots__ = ("label", "candidates", "engines")

    def __init__(self, label: str, candidates: tuple[str, ...], engines: tuple[str, ...] = ()) -> None:
        self.label = label
        self.candidates = candidates
        self.engines = engines


CRITICAL_GROUPS = (
    CriticalGroup(f"движок Zapret 1 ({EXE_NAME_WINWS1})", (f"exe/{EXE_NAME_WINWS1}",), (ENGINE_WINWS1,)),
    CriticalGroup(f"движок Zapret 2 ({EXE_NAME_WINWS2})", (f"exe/{EXE_NAME_WINWS2}",), (ENGINE_WINWS2,)),
    CriticalGroup("библиотека WinDivert.dll", ("exe/WinDivert.dll",)),
    CriticalGroup(
        "драйвер WinDivert",
        tuple(f"exe/{name}" for name in _WINDIVERT_DRIVER_NAMES),
    ),
)


def normalize_relative_path(value: str | os.PathLike[str]) -> str:
    return str(value).replace("\\", "/").strip("/")


def manifest_path(manifest_dir: str | os.PathLike[str] | None = None) -> Path:
    """Путь к манифесту. По умолчанию — ``_internal`` установленной программы."""
    if manifest_dir is None:
        from config.runtime_layout import APPLICATION_PATHS

        return Path(APPLICATION_PATHS.runtime_dir) / MANIFEST_FILE_NAME
    return Path(manifest_dir) / MANIFEST_FILE_NAME


def parse_manifest(payload: object) -> InstallManifest | None:
    if not isinstance(payload, dict):
        return None
    try:
        schema = int(payload.get("schema") or 0)
    except (TypeError, ValueError):
        return None
    if schema != MANIFEST_SCHEMA_VERSION:
        return None

    entries: list[ManifestEntry] = []
    for item in payload.get("files") or ():
        if not isinstance(item, dict):
            continue
        path = normalize_relative_path(str(item.get("path") or ""))
        if not path:
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            continue
        role = str(item.get("role") or ROLE_REQUIRED).strip().lower()
        if role not in (ROLE_CRITICAL, ROLE_REQUIRED):
            role = ROLE_REQUIRED
        engines = tuple(
            str(engine).strip().lower()
            for engine in (item.get("engines") or ())
            if str(engine).strip()
        )
        entries.append(
            ManifestEntry(
                path=path,
                size=max(size, 0),
                sha256=str(item.get("sha256") or "").strip().lower(),
                role=role,
                engines=engines,
            )
        )

    if not entries:
        return None

    return InstallManifest(
        schema=schema,
        version=str(payload.get("version") or "").strip(),
        channel=str(payload.get("channel") or "").strip().lower(),
        entries=tuple(entries),
    )


def load_manifest(manifest_dir: str | os.PathLike[str] | None = None) -> InstallManifest | None:
    """Читает манифест установки. Любая проблема чтения — это ``None``.

    Отсутствие манифеста никогда не должно мешать работе: так выглядит запуск
    из исходников и сборка, выпущенная до появления манифеста.
    """
    path = manifest_path(manifest_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parse_manifest(payload)


def _role_and_engines(relative_path: str) -> tuple[str, tuple[str, ...]]:
    for group in CRITICAL_GROUPS:
        if relative_path in group.candidates:
            return ROLE_CRITICAL, group.engines
    return ROLE_REQUIRED, ()


def build_manifest(
    stage_root: str | os.PathLike[str],
    *,
    version: str,
    channel: str,
    scopes: tuple[str, ...] = MANIFEST_SCOPES,
) -> InstallManifest:
    """Строит манифест по подготовленному дереву поставки."""
    root = Path(stage_root)
    entries: list[ManifestEntry] = []
    for scope in scopes:
        scope_root = root / scope
        if not scope_root.is_dir():
            continue
        for path in sorted(scope_root.rglob("*")):
            if not path.is_file():
                continue
            relative = normalize_relative_path(path.relative_to(root))
            role, engines = _role_and_engines(relative)
            entries.append(
                ManifestEntry(
                    path=relative,
                    size=path.stat().st_size,
                    sha256=sha256_file(path),
                    role=role,
                    engines=engines,
                )
            )
    return InstallManifest(
        schema=MANIFEST_SCHEMA_VERSION,
        version=str(version or "").strip(),
        channel=str(channel or "").strip().lower(),
        entries=tuple(entries),
    )


def missing_critical_groups(stage_root: str | os.PathLike[str]) -> tuple[str, ...]:
    """Названия групп, ни один кандидат которых не найден в поставке.

    Сборка обязана падать на непустом результате: иначе можно выпустить релиз
    без движка, и все, кто на него обновится, останутся без запуска.
    """
    root = Path(stage_root)
    missing: list[str] = []
    for group in CRITICAL_GROUPS:
        if not any((root / candidate).is_file() for candidate in group.candidates):
            missing.append(group.label)
    return tuple(missing)


def dump_manifest(manifest: InstallManifest) -> str:
    return json.dumps(manifest.to_json(), ensure_ascii=False, indent=2, sort_keys=False)


def write_manifest(manifest: InstallManifest, destination: str | os.PathLike[str]) -> Path:
    path = Path(destination)
    if path.is_dir():
        path = path / MANIFEST_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_manifest(manifest) + "\n", encoding="utf-8")
    return path


__all__ = [
    "CRITICAL_GROUPS",
    "CriticalGroup",
    "MANIFEST_SCOPES",
    "build_manifest",
    "dump_manifest",
    "load_manifest",
    "manifest_path",
    "missing_critical_groups",
    "normalize_relative_path",
    "parse_manifest",
    "write_manifest",
]
