from __future__ import annotations

"""Проверка установленной поставки по манифесту.

Быстрая проверка (наличие + размер) стоит единицы миллисекунд и вызывается
перед каждым запуском DPI. Полная проверка (SHA-256) — только в фоне после
обновления, когда есть реальный повод пересчитывать хэши.
"""

import os
from pathlib import Path

from utils.file_digest import sha256_file

from .manifest import MANIFEST_SCOPES, load_manifest
from .models import (
    InstallManifest,
    IntegrityCause,
    IntegrityFinding,
    IntegrityReport,
    KIND_CORRUPTED,
    KIND_MISSING,
)


def _install_root(root: str | os.PathLike[str] | None) -> Path:
    if root is not None:
        return Path(root)
    from config.runtime_layout import APPLICATION_PATHS

    return Path(APPLICATION_PATHS.root)


def _manifest_dir(install_root: Path, manifest_dir: str | os.PathLike[str] | None) -> Path:
    if manifest_dir is not None:
        return Path(manifest_dir)
    from config.runtime_layout import RUNTIME_DIR_NAME

    return install_root / RUNTIME_DIR_NAME


def _scope_of(relative_path: str) -> str:
    head = relative_path.split("/", 1)[0]
    return head if head in MANIFEST_SCOPES else ""


def _classify(
    install_root: Path,
    findings: tuple[IntegrityFinding, ...],
    *,
    total_entries: int,
) -> IntegrityCause:
    if not findings:
        return IntegrityCause.OK

    missing = tuple(f for f in findings if f.kind == KIND_MISSING)
    if not missing:
        return IntegrityCause.CORRUPTED

    # Пропала вся поставка целиком — это не «файл удалили», а сломанная
    # или незавершённая установка.
    if total_entries and len(missing) >= total_entries:
        return IntegrityCause.INCOMPLETE_INSTALL

    # Каталога поставки нет вообще — установка не доложила свою часть.
    for finding in missing:
        scope = _scope_of(finding.path)
        if scope and not (install_root / scope).is_dir():
            return IntegrityCause.INCOMPLETE_INSTALL

    # Каталоги на месте, отдельные файлы исчезли: их удалили уже после
    # установки. На практике это почти всегда антивирус.
    return IntegrityCause.REMOVED_AFTER_INSTALL


def verify_installation(
    *,
    root: str | os.PathLike[str] | None = None,
    manifest_dir: str | os.PathLike[str] | None = None,
    manifest: InstallManifest | None = None,
    deep: bool = False,
) -> IntegrityReport:
    install_root = _install_root(root)
    if manifest is None:
        manifest = load_manifest(_manifest_dir(install_root, manifest_dir))
    if manifest is None:
        return IntegrityReport(cause=IntegrityCause.MANIFEST_ABSENT, deep=bool(deep))

    findings: list[IntegrityFinding] = []
    for entry in manifest.entries:
        target = install_root / entry.path
        try:
            stat_result = target.stat()
        except OSError:
            findings.append(
                IntegrityFinding(
                    path=entry.path,
                    kind=KIND_MISSING,
                    role=entry.role,
                    engines=entry.engines,
                )
            )
            continue

        if entry.size and stat_result.st_size != entry.size:
            findings.append(
                IntegrityFinding(
                    path=entry.path,
                    kind=KIND_CORRUPTED,
                    role=entry.role,
                    engines=entry.engines,
                )
            )
            continue

        if not deep or not entry.sha256:
            continue

        try:
            actual = sha256_file(target)
        except OSError:
            findings.append(
                IntegrityFinding(
                    path=entry.path,
                    kind=KIND_MISSING,
                    role=entry.role,
                    engines=entry.engines,
                )
            )
            continue

        if actual != entry.sha256:
            findings.append(
                IntegrityFinding(
                    path=entry.path,
                    kind=KIND_CORRUPTED,
                    role=entry.role,
                    engines=entry.engines,
                )
            )

    frozen = tuple(findings)
    return IntegrityReport(
        cause=_classify(install_root, frozen, total_entries=len(manifest.entries)),
        findings=frozen,
        manifest_version=manifest.version,
        checked_files=len(manifest.entries),
        deep=bool(deep),
    )


def verify_fast(**kwargs) -> IntegrityReport:
    return verify_installation(deep=False, **kwargs)


def verify_full(**kwargs) -> IntegrityReport:
    return verify_installation(deep=True, **kwargs)


__all__ = ["verify_fast", "verify_full", "verify_installation"]
