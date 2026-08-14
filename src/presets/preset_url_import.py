"""Скачивание пресета по ссылке для диалога импорта.

Модуль без Qt: вся сетевая работа выполняется вызывающим воркером
в отдельном потоке. Скачанный файл кладётся во временную папку с
маркерным именем, чтобы вызывающая сторона могла отличить «свой»
temp-файл и убрать его после импорта.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

from updater.proxy_bypass import request_get_bypass_proxy
from updater.server_config import CONNECT_TIMEOUT, READ_TIMEOUT

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_DOWNLOAD_DIR_PREFIX = "zapret-preset-url-"
_USER_AGENT = "Zapret-PresetImport/1.0"
_ZIP_MAGIC = b"PK\x03\x04"

ERROR_KIND_URL = "url"
ERROR_KIND_SSL = "ssl"
ERROR_KIND_TIMEOUT = "timeout"
ERROR_KIND_NETWORK = "network"
ERROR_KIND_HTTP = "http"
ERROR_KIND_TOO_LARGE = "too_large"
ERROR_KIND_CONTENT = "content"
ERROR_KIND_CANCELLED = "cancelled"


class PresetUrlDownloadError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = str(kind or ERROR_KIND_NETWORK)
        self.detail = str(detail or "")


@dataclass(frozen=True, slots=True)
class DownloadedPresetFile:
    file_path: str
    source_url: str
    suffix: str  # ".txt" | ".zip"


def validate_preset_import_url(url: str) -> str:
    """Возвращает пустую строку, если URL пригоден для импорта, иначе причину."""
    text = str(url or "").strip()
    if not text:
        return "Ссылка пуста"
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        return "Поддерживаются только ссылки http:// и https://"
    if not parts.netloc:
        return "В ссылке не указан адрес сервера"
    return ""


def is_https_preset_import_url(url: str) -> bool:
    return urlsplit(str(url or "").strip()).scheme.lower() == "https"


def _sanitize_stem(value: str) -> str:
    text = str(value or "").strip()
    sanitized = re.sub(r'[\\/:*?"<>|\x00]+', "_", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip().rstrip(".")
    return sanitized[:100]


def _stem_from_content_disposition(header_value: str) -> str:
    text = str(header_value or "")
    match = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", text, re.IGNORECASE)
    if match:
        return Path(unquote(match.group(1).strip().strip('"'))).stem
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', text, re.IGNORECASE)
    if match:
        return Path(match.group(1).strip()).stem
    return ""


def preset_file_stem_from_url(url: str, headers=None) -> str:
    """Имя пресета: Content-Disposition → последний сегмент URL → "Imported"."""
    headers = headers or {}
    stem = _stem_from_content_disposition(headers.get("Content-Disposition", ""))
    if not stem:
        path = urlsplit(str(url or "").strip()).path
        last_segment = unquote(path.rstrip("/").rsplit("/", 1)[-1]) if path else ""
        stem = Path(last_segment).stem
    sanitized = _sanitize_stem(stem)
    return sanitized or "Imported"


def _resolve_suffix(url: str, headers, first_chunk: bytes) -> str:
    if first_chunk.startswith(_ZIP_MAGIC):
        return ".zip"
    path_suffix = Path(urlsplit(str(url or "")).path).suffix.lower()
    if path_suffix == ".zip":
        return ".zip"
    content_type = str((headers or {}).get("Content-Type", "")).lower()
    if "zip" in content_type:
        return ".zip"
    return ".txt"


def download_preset_from_url(url: str, *, cancel_cb=None) -> DownloadedPresetFile:
    """Скачивает пресет по ссылке во временный файл.

    Бросает PresetUrlDownloadError с kind для локализуемого сообщения.
    Возвращённый файл лежит в отдельной managed-папке — после импорта
    вызывающая сторона обязана позвать cleanup_download().
    """
    source_url = str(url or "").strip()
    validation_error = validate_preset_import_url(source_url)
    if validation_error:
        raise PresetUrlDownloadError(ERROR_KIND_URL, validation_error)

    def _cancelled() -> bool:
        return bool(cancel_cb and cancel_cb())

    try:
        response = request_get_bypass_proxy(
            source_url,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"},
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            verify=True,
            stream=True,
        )
    except requests.exceptions.SSLError as exc:
        raise PresetUrlDownloadError(ERROR_KIND_SSL, str(exc)) from exc
    except requests.exceptions.Timeout as exc:
        raise PresetUrlDownloadError(ERROR_KIND_TIMEOUT, str(exc)) from exc
    except requests.exceptions.RequestException as exc:
        raise PresetUrlDownloadError(ERROR_KIND_NETWORK, str(exc)) from exc

    try:
        if int(response.status_code) >= 400:
            raise PresetUrlDownloadError(ERROR_KIND_HTTP, f"HTTP {response.status_code}")

        content_length = str(response.headers.get("Content-Length", "") or "").strip()
        if content_length.isdigit() and int(content_length) > MAX_DOWNLOAD_BYTES:
            raise PresetUrlDownloadError(
                ERROR_KIND_TOO_LARGE,
                f"Content-Length {content_length} > {MAX_DOWNLOAD_BYTES}",
            )

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                if _cancelled():
                    raise PresetUrlDownloadError(ERROR_KIND_CANCELLED, "Импорт отменён")
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise PresetUrlDownloadError(
                        ERROR_KIND_TOO_LARGE,
                        f"Файл больше {MAX_DOWNLOAD_BYTES} байт",
                    )
                chunks.append(chunk)
        except requests.exceptions.Timeout as exc:
            raise PresetUrlDownloadError(ERROR_KIND_TIMEOUT, str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise PresetUrlDownloadError(ERROR_KIND_NETWORK, str(exc)) from exc

        payload = b"".join(chunks)
        if not payload.strip():
            raise PresetUrlDownloadError(ERROR_KIND_CONTENT, "Файл по ссылке пуст")

        suffix = _resolve_suffix(source_url, response.headers, payload[:8])
        stem = preset_file_stem_from_url(source_url, response.headers)
    finally:
        response.close()

    download_dir = Path(tempfile.mkdtemp(prefix=_DOWNLOAD_DIR_PREFIX))
    file_path = download_dir / f"{stem}{suffix}"
    file_path.write_bytes(payload)
    return DownloadedPresetFile(
        file_path=str(file_path),
        source_url=source_url,
        suffix=suffix,
    )


def is_managed_download_path(file_path: str | Path) -> bool:
    try:
        return Path(file_path).parent.name.startswith(_DOWNLOAD_DIR_PREFIX)
    except (TypeError, ValueError):
        return False


def cleanup_download(file_path: str | Path) -> None:
    """Удаляет managed-папку скачивания; чужие пути не трогает."""
    if not is_managed_download_path(file_path):
        return
    shutil.rmtree(Path(file_path).parent, ignore_errors=True)
