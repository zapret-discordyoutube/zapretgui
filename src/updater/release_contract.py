"""Единый обязательный набор метаданных установщика обновления."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit


SHA256_HEX_LENGTH = 64


class ReleaseMetadataError(ValueError):
    """Источник не описал готовый к скачиванию и проверке установщик."""


def normalize_sha256(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != SHA256_HEX_LENGTH:
        return ""
    try:
        int(normalized, 16)
    except ValueError:
        return ""
    return normalized


@dataclass(frozen=True, slots=True)
class ReleaseArtifactMetadata:
    """Проверенные поля одного выпуска из одного источника."""

    version: str
    update_url: str
    file_name: str
    file_size: int
    sha256: str
    verify_ssl: bool

    @classmethod
    def from_mapping(cls, release_info: Mapping[str, Any]) -> "ReleaseArtifactMetadata":
        version = str(release_info.get("version") or "").strip()
        if not version:
            raise ReleaseMetadataError("В метаданных выпуска нет версии")

        update_url = str(release_info.get("update_url") or "").strip()
        try:
            parsed_url = urlsplit(update_url)
        except Exception as exc:
            raise ReleaseMetadataError("В метаданных выпуска некорректная ссылка") from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ReleaseMetadataError("В метаданных выпуска некорректная ссылка")

        file_name = str(release_info.get("file_name") or "").strip()
        remote_name = PurePosixPath(unquote(parsed_url.path)).name
        if not file_name:
            raise ReleaseMetadataError("В метаданных выпуска нет имени файла")
        if remote_name != file_name or PurePosixPath(file_name).name != file_name:
            raise ReleaseMetadataError("Ссылка выпуска не соответствует имени файла")

        try:
            file_size = int(release_info.get("file_size") or 0)
        except (TypeError, ValueError) as exc:
            raise ReleaseMetadataError("В метаданных выпуска нет размера файла") from exc
        if file_size <= 0:
            raise ReleaseMetadataError("В метаданных выпуска нет размера файла")

        sha256 = normalize_sha256(release_info.get("sha256"))
        if not sha256:
            raise ReleaseMetadataError("В метаданных выпуска нет SHA-256")

        return cls(
            version=version,
            update_url=update_url,
            file_name=file_name,
            file_size=file_size,
            sha256=sha256,
            verify_ssl=bool(release_info.get("verify_ssl", True)),
        )


def is_installable_release(release_info: Mapping[str, Any]) -> bool:
    try:
        ReleaseArtifactMetadata.from_mapping(release_info)
    except (ReleaseMetadataError, AttributeError, TypeError):
        return False
    return True


__all__ = [
    "ReleaseArtifactMetadata",
    "ReleaseMetadataError",
    "is_installable_release",
    "normalize_sha256",
]
