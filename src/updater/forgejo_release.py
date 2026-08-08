"""Получение выпусков ZapretGUI из собственного Forgejo.

Forgejo хранит установщик и отдельный файл ``<installer>.sha256`` в одном
выпуске. Клиент принимает только точные ссылки собственного домена и только
пару из этих двух файлов. Так контрольная сумма не зависит от необязательных
полей API и проверяет уже скачанные байты установщика.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit
import re
import time

import requests
from packaging import version

from log.log import log

from .channel_utils import is_dev_release_asset_name, normalize_update_channel
from .forgejo_cache_storage import load_forgejo_cache, save_forgejo_cache
from .network_hints import maybe_log_disable_dpi_for_update
from .proxy_bypass import request_get_bypass_proxy


FORGEJO_ORIGIN = "https://git.zapret.moe"
FORGEJO_REPOSITORY = "zapretdiscordyoutube/zapretgui"
FORGEJO_API_URL = f"{FORGEJO_ORIGIN}/api/v1/repos/{FORGEJO_REPOSITORY}/releases"
FORGEJO_REPOSITORY_API_URL = f"{FORGEJO_ORIGIN}/api/v1/repos/{FORGEJO_REPOSITORY}"
FORGEJO_RELEASE_DOWNLOAD_PREFIX = f"/{FORGEJO_REPOSITORY}/releases/download/"
TIMEOUT = 10
CACHE_TTL = 300
ALL_RELEASES_CACHE_TTL = 600
MAX_RELEASE_PAGES = 4
MAX_SIDECAR_BYTES = 4096

_SHA256_LINE_RE = re.compile(r"\A([0-9a-fA-F]{64})  ([^\r\n]+)\r?\n?\Z")
_forgejo_cache: Dict[str, Tuple[Any, float]] = {}
_all_releases_cache: Tuple[List[Dict[str, Any]], float] = ([], 0)


def normalize_version(ver_str: str) -> str:
    value = str(ver_str or "").strip()
    if value.startswith(("v", "V")):
        value = value[1:]
    parts = value.split(".")
    if len(parts) < 2 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid version format: {value}")
    return value


def compare_versions(v1: str, v2: str) -> int:
    try:
        first = version.parse(v1)
        second = version.parse(v2)
        return -1 if first < second else (1 if first > second else 0)
    except Exception:
        return -1 if v1 < v2 else (1 if v1 > v2 else 0)


def _load_persistent_cache() -> None:
    global _forgejo_cache
    try:
        raw = load_forgejo_cache()
        now = time.time()
        cache: Dict[str, Tuple[Any, float]] = {}
        if isinstance(raw, dict):
            for url, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                timestamp = float(entry.get("timestamp", 0) or 0)
                if now - timestamp < CACHE_TTL:
                    cache[str(url)] = (entry.get("content"), timestamp)
        _forgejo_cache = cache
        if cache:
            log(f"📦 Загружено {len(cache)} записей Forgejo из кэша", "🔄 CACHE")
    except Exception as exc:
        log(f"Ошибка загрузки кэша Forgejo: {exc}", "⚠️ CACHE")
        _forgejo_cache = {}


def _save_persistent_cache() -> None:
    try:
        save_forgejo_cache(
            {
                url: {"content": content, "timestamp": timestamp}
                for url, (content, timestamp) in _forgejo_cache.items()
            }
        )
    except Exception as exc:
        log(f"Ошибка сохранения кэша Forgejo: {exc}", "⚠️ CACHE")


def _request_json(url: str, timeout: int = TIMEOUT) -> Any:
    cached = _forgejo_cache.get(url)
    if cached and time.time() - cached[1] < CACHE_TTL:
        return cached[0]

    headers = {
        "Accept": "application/json",
        "User-Agent": "Zapret-Updater/3.1",
    }
    try:
        response = request_get_bypass_proxy(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        _forgejo_cache[url] = (data, time.time())
        _save_persistent_cache()
        return data
    except Exception as exc:
        log(f"⚠️ Ошибка запроса к Forgejo: {exc}", "⚠️ UPDATE")
        maybe_log_disable_dpi_for_update(exc, scope="update_check", level="⚠️ UPDATE")
        return cached[0] if cached else None


def _trusted_release_asset_url(url: str, *, tag_name: str, file_name: str) -> bool:
    try:
        parsed = urlsplit(str(url or ""))
    except Exception:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != "git.zapret.moe"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    expected_prefix = f"{FORGEJO_RELEASE_DOWNLOAD_PREFIX}{tag_name}/"
    decoded_path = unquote(parsed.path)
    if not decoded_path.startswith(expected_prefix):
        return False
    relative = decoded_path[len(expected_prefix) :]
    return relative == file_name and PurePosixPath(relative).name == relative


def _parse_sha256_sidecar(text: str, *, expected_name: str) -> str:
    match = _SHA256_LINE_RE.fullmatch(str(text or ""))
    if not match or match.group(2) != expected_name:
        raise ValueError("Некорректный файл контрольной суммы выпуска")
    return match.group(1).lower()


def _fetch_release_sha256(release: Dict[str, Any], exe_asset: Dict[str, Any]) -> str:
    tag_name = str(release.get("tag_name") or "")
    file_name = str(exe_asset.get("name") or "")
    exe_url = str(exe_asset.get("browser_download_url") or "")
    if not _trusted_release_asset_url(exe_url, tag_name=tag_name, file_name=file_name):
        raise ValueError("Forgejo вернул недоверенную ссылку установщика")

    sidecar_name = f"{file_name}.sha256"
    sidecar = next(
        (asset for asset in release.get("assets", []) if asset.get("name") == sidecar_name),
        None,
    )
    if not sidecar:
        raise ValueError(f"В выпуске отсутствует {sidecar_name}")
    sidecar_url = str(sidecar.get("browser_download_url") or "")
    if not _trusted_release_asset_url(sidecar_url, tag_name=tag_name, file_name=sidecar_name):
        raise ValueError("Forgejo вернул недоверенную ссылку контрольной суммы")

    response = request_get_bypass_proxy(
        sidecar_url,
        headers={"Accept": "text/plain", "User-Agent": "Zapret-Updater/3.1"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    content = response.content
    if len(content) > MAX_SIDECAR_BYTES:
        raise ValueError("Файл контрольной суммы слишком большой")
    return _parse_sha256_sidecar(content.decode("ascii"), expected_name=file_name)


def _release_to_result(release: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    exe_asset = next(
        (
            asset
            for asset in release.get("assets", [])
            if str(asset.get("name") or "").lower().endswith(".exe")
        ),
        None,
    )
    if not exe_asset:
        return None
    try:
        normalized = normalize_version(str(release.get("tag_name") or ""))
        checksum = _fetch_release_sha256(release, exe_asset)
    except Exception as exc:
        log(f"❌ Выпуск Forgejo отклонён: {exc}", "🔁 UPDATE")
        return None
    return {
        "version": normalized,
        "tag_name": release.get("tag_name"),
        "update_url": exe_asset.get("browser_download_url"),
        "file_name": exe_asset.get("name"),
        "file_size": exe_asset.get("size"),
        "sha256": checksum,
        "release_notes": release.get("body", ""),
        "prerelease": bool(release.get("prerelease", False)),
        "name": release.get("name", ""),
        "published_at": release.get("published_at", ""),
        "created_at": release.get("created_at", ""),
    }


def get_all_releases_with_exe() -> List[Dict[str, Any]]:
    global _all_releases_cache
    cached, cached_at = _all_releases_cache
    if cached and time.time() - cached_at < ALL_RELEASES_CACHE_TTL:
        return cached
    if not _forgejo_cache:
        _load_persistent_cache()

    result: List[Dict[str, Any]] = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        releases = _request_json(f"{FORGEJO_API_URL}?limit=50&page={page}")
        if not isinstance(releases, list) or not releases:
            break
        for release in releases:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            normalized = _release_to_result(release)
            if normalized:
                result.append(normalized)
        if len(releases) < 50:
            break
    if result:
        _all_releases_cache = (result, time.time())
    return result


def get_latest_release(channel: str) -> Optional[dict]:
    if not _forgejo_cache:
        _load_persistent_cache()
    selected = normalize_update_channel(channel)
    if selected == "stable":
        release = _request_json(f"{FORGEJO_API_URL}/latest")
        return _release_to_result(release) if isinstance(release, dict) else None

    releases = [
        item
        for item in get_all_releases_with_exe()
        if item.get("prerelease") or is_dev_release_asset_name(item.get("file_name", ""))
    ]
    releases.sort(key=lambda item: version.parse(item["version"]), reverse=True)
    return releases[0] if releases else None


def check_api() -> Dict[str, Any]:
    started = time.time()
    data = _request_json(FORGEJO_REPOSITORY_API_URL, timeout=5)
    if not isinstance(data, dict) or data.get("full_name") != FORGEJO_REPOSITORY:
        raise RuntimeError("Forgejo API не вернул ожидаемый репозиторий")
    return {"online": True, "response_time": time.time() - started}


__all__ = [
    "FORGEJO_API_URL",
    "check_api",
    "compare_versions",
    "get_all_releases_with_exe",
    "get_latest_release",
    "normalize_version",
]
