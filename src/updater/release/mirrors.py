from __future__ import annotations

"""Зеркала обновлений (VPS) — запасной источник выпуска и адреса скачивания.

Решение владельца проекта: если Forgejo ответил ошибкой, зеркало может дать
полный ответ о выпуске — версию, имя файла, размер и SHA-256 из одного
``all_versions.json``. Поля разных источников не смешиваются никогда: ответ
либо целиком от Forgejo, либо целиком от одного зеркала. Сертификаты зеркал
самоподписанные, поэтому такой ответ слабее ответа Forgejo — он используется
только когда Forgejo недоступен.

Зеркала опрашиваются параллельно, первый корректный ответ побеждает. Мёртвое
зеркало не задерживает остальные и не «застревает» выбранным.
"""

import time
from collections.abc import Callable
from typing import Any

import urllib3

from log.log import log

from ..channel_utils import is_dev_update_channel, normalize_update_channel
from ..network_hints import maybe_log_disable_dpi_for_update
from ..release_contract import ReleaseArtifactMetadata
from ..server_config import VPS_SERVERS, should_verify_ssl
from ..versions import normalize_version
from .http import MIRROR_TIMEOUT, first_success, new_session, short_error


MIRRORS_DEADLINE_SECONDS = 20.0

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class MirrorReleaseError(RuntimeError):
    """Ни одно зеркало не дало корректного выпуска."""


def mirror_servers() -> list[dict[str, Any]]:
    """Зеркала в порядке из конфигурации сборки."""
    return [dict(server) for server in VPS_SERVERS]


def _endpoints(server: dict[str, Any]) -> tuple[tuple[str, str, bool], ...]:
    return (
        ("HTTPS", f"https://{server['host']}:{server['https_port']}", bool(should_verify_ssl())),
        ("HTTP", f"http://{server['host']}:{server['http_port']}", False),
    )


def fetch_versions(server: dict[str, Any]) -> tuple[dict[str, Any], str, str, bool, float]:
    """``all_versions.json`` зеркала: сначала HTTPS, затем HTTP.

    Возвращает данные, протокол, базовый адрес, проверку сертификата и время
    ответа. Если не ответил ни один протокол — исключение с причинами обоих.
    """
    errors: list[str] = []
    session = new_session()
    try:
        for protocol, base_url, verify_ssl in _endpoints(server):
            started = time.monotonic()
            try:
                response = session.get(
                    f"{base_url}/api/all_versions.json",
                    timeout=MIRROR_TIMEOUT,
                    verify=verify_ssl,
                    headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                maybe_log_disable_dpi_for_update(exc, scope="update_check", level="🔄 RELEASE")
                errors.append(f"{protocol}: {short_error(exc)}")
                continue
            if not isinstance(data, dict):
                errors.append(f"{protocol}: неожиданный ответ")
                continue
            return data, protocol, base_url, verify_ssl, time.monotonic() - started
    finally:
        session.close()
    raise MirrorReleaseError("; ".join(errors) or "нет ответа")


def release_from_versions(
    data: dict[str, Any],
    channel: str,
    *,
    base_url: str,
    verify_ssl: bool,
    source: str,
) -> dict[str, Any]:
    """Выпуск канала из ``all_versions.json`` одного зеркала, строго проверенный."""
    selected = normalize_update_channel(channel)
    entry = data.get(selected)
    if not isinstance(entry, dict) or not entry.get("version"):
        raise MirrorReleaseError(f"в ответе нет канала {selected}")
    version = normalize_version(str(entry.get("version") or ""))
    file_name = str(entry.get("file_name") or "").strip()
    release = {
        "version": version,
        "tag_name": version,
        "update_url": f"{base_url}/download/{file_name}",
        "file_name": file_name,
        "file_size": entry.get("file_size"),
        "sha256": entry.get("sha256"),
        "release_notes": str(entry.get("release_notes") or ""),
        "prerelease": is_dev_update_channel(selected),
        "name": f"Zapret {version} ({selected})",
        "published_at": str(entry.get("date") or ""),
        "source": source,
        "verify_ssl": bool(verify_ssl),
    }
    # Неполный ответ — ошибка этого зеркала, а не повод дополнять его чужими полями.
    ReleaseArtifactMetadata.from_mapping(release)
    return release


def _mirror_job(server: dict[str, Any], channel: str) -> Callable[[], dict[str, Any]]:
    def job() -> dict[str, Any]:
        name = str(server.get("name") or server.get("host"))
        try:
            data, protocol, base_url, verify_ssl, _elapsed = fetch_versions(server)
            return release_from_versions(
                data,
                channel,
                base_url=base_url,
                verify_ssl=verify_ssl,
                source=f"{name} ({protocol})",
            )
        except Exception as exc:
            raise MirrorReleaseError(f"{name}: {short_error(exc)}") from exc

    return job


def fetch_latest_release(
    channel: str,
    *,
    servers: list[dict[str, Any]] | None = None,
    timeout: float = MIRRORS_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Первый корректный выпуск канала от любого зеркала."""
    selected = servers if servers is not None else mirror_servers()
    if not selected:
        raise MirrorReleaseError("зеркала не настроены")
    release, errors = first_success(
        [_mirror_job(server, channel) for server in selected],
        timeout=timeout,
        name="update-mirror",
    )
    if release is not None:
        log(f"✅ Зеркало {release['source']}: выпуск {release['version']}", "🔄 RELEASE")
        return release
    reasons = "; ".join(str(error) for error in errors) or "нет ответа за отведённое время"
    raise MirrorReleaseError(reasons)


def download_sources(file_name: str) -> list[tuple[str, bool]]:
    """Адреса того же файла на зеркалах: сначала HTTPS всех зеркал, затем HTTP."""
    https = [
        (f"https://{server['host']}:{server['https_port']}/download/{file_name}", bool(should_verify_ssl()))
        for server in VPS_SERVERS
    ]
    http = [
        (f"http://{server['host']}:{server['http_port']}/download/{file_name}", False)
        for server in VPS_SERVERS
    ]
    return https + http


__all__ = [
    "MIRRORS_DEADLINE_SECONDS",
    "MirrorReleaseError",
    "download_sources",
    "fetch_latest_release",
    "fetch_versions",
    "mirror_servers",
    "release_from_versions",
]
