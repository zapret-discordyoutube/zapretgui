from __future__ import annotations

"""Скачивание установщика и проверка его байтов.

Порядок:

1. Файл качается сразу со всех источников (``engine``) прямо в закрытый
   каталог состояния обновления: кто быстрее, тот и отдаёт больше.
2. Готовый файл сверяется по размеру и SHA-256 из данных выпуска.
3. Если собранный из нескольких источников файл проверку не прошёл, значит
   один из них отдаёт испорченные байты. Тогда файл качается заново с
   каждого источника по отдельности — испорченное зеркало не мешает
   исправному.

Ошибка записи на диск (нет места, файл занят) и отмена останавливают
загрузку сразу: другие источники тут не помогут.
"""

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import urllib3

from log.log import log
from utils.file_digest import sha256_file

from ..release import mirrors
from ..release_contract import ReleaseArtifactMetadata
from .contracts import (
    CancellationToken,
    DownloadSource,
    LocalWriteError,
    UpdateArtifact,
    UpdateCancelled,
    UpdateIntegrityError,
    UpdatePipelineError,
)
from .engine import MultiSourceDownload


HASH_CHUNK_SIZE = 1024 * 1024
LOG_LEVEL = "🔁 UPDATE"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def build_download_sources(metadata: ReleaseArtifactMetadata) -> tuple[DownloadSource, ...]:
    """Источник из ответа выпуска и зеркала того же проверенного файла.

    Один сервер — один источник: зеркало, которое само дало ответ о выпуске,
    второй раз в список не попадает.
    """
    primary_host = _host(metadata.update_url)
    sources = [DownloadSource(metadata.update_url, metadata.verify_ssl)]
    for mirror in mirrors.download_sources(metadata.file_name):
        if mirror.host == primary_host:
            sources[0] = DownloadSource(metadata.update_url, metadata.verify_ssl, name=mirror.name)
            continue
        sources.append(
            DownloadSource(mirror.https_url, mirror.verify_ssl, name=mirror.name, fallback_url=mirror.http_url)
        )
    return tuple(sources)


def verify_file(path: str | os.PathLike[str], artifact: UpdateArtifact, token: CancellationToken) -> None:
    token.checkpoint()
    actual_size = os.path.getsize(path)
    if actual_size != artifact.expected_size:
        raise UpdateIntegrityError(
            f"Размер установщика не совпадает: {actual_size} вместо {artifact.expected_size}"
        )
    actual_sha256 = sha256_file(path, checkpoint=token.checkpoint, chunk_size=HASH_CHUNK_SIZE)
    if actual_sha256 != artifact.expected_sha256:
        raise UpdateIntegrityError("SHA-256 установщика не совпадает с данными выпуска")


def _download_and_verify(
    sources: tuple[DownloadSource, ...],
    target: Path,
    artifact: UpdateArtifact,
    *,
    token: CancellationToken,
    on_progress: Callable[[int, int, int], None] | None,
) -> frozenset[str]:
    """Одна попытка. При несовпадении суммы возвращает имена давших байты источников."""
    contributors = MultiSourceDownload(
        sources,
        target,
        total=artifact.expected_size,
        token=token,
        on_progress=on_progress,
    ).run()
    try:
        verify_file(target, artifact, token)
    except UpdateIntegrityError:
        return contributors
    return frozenset()


def download_artifact(
    artifact: UpdateArtifact,
    path: str | os.PathLike[str],
    *,
    token: CancellationToken,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> None:
    """Скачивает и проверяет установщик в ``path``."""
    target = Path(path)
    suspects = _download_and_verify(artifact.sources, target, artifact, token=token, on_progress=on_progress)
    if not suspects:
        log(f"✅ Установщик скачан и проверен: {artifact.expected_sha256}", LOG_LEVEL)
        return

    log("Файл не прошёл проверку SHA-256 — качаем с каждого источника отдельно", "WARNING")
    errors = ["SHA-256 установщика не совпадает с данными выпуска"]
    # Единственный участник неудачной попытки уже показал, что отдаёт не то.
    alone = next(iter(suspects)) if len(suspects) == 1 else None
    for source in artifact.sources:
        token.checkpoint()
        name = source.label
        if name == alone:
            continue
        try:
            failed = _download_and_verify((source,), target, artifact, token=token, on_progress=on_progress)
        except (UpdateCancelled, LocalWriteError):
            raise
        except UpdatePipelineError as exc:
            errors.append(str(exc))
            continue
        if not failed:
            log(f"✅ Установщик скачан с {name} и проверен: {artifact.expected_sha256}", LOG_LEVEL)
            return
    raise UpdatePipelineError("Не удалось скачать обновление: " + "; ".join(dict.fromkeys(errors)))


__all__ = [
    "CancellationToken",
    "DownloadSource",
    "LocalWriteError",
    "UpdateArtifact",
    "UpdateCancelled",
    "UpdateIntegrityError",
    "UpdatePipelineError",
    "build_download_sources",
    "download_artifact",
    "verify_file",
]
