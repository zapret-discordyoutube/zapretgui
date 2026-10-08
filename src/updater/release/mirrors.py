from __future__ import annotations

"""Зеркала обновлений (VPS) — запасной источник выпуска и адреса скачивания.

Решение владельца проекта: если Forgejo ответил ошибкой, зеркало может дать
полный ответ о выпуске — версию, имя файла, размер и SHA-256 из одного
``all_versions.json``. Поля разных источников не смешиваются никогда: ответ
либо целиком от Forgejo, либо целиком от одного зеркала. Сертификаты зеркал
самоподписанные, поэтому такой ответ слабее ответа Forgejo — он используется
только когда Forgejo недоступен.

Зеркала опрашиваются одновременно. Первый корректный ответ не побеждает
сразу: остальным даётся короткое время ответить, и берётся самая новая
версия. Иначе отставшее зеркало, ответив первым, выдало бы старый выпуск за
новейший. Мёртвое зеркало никого не задерживает дольше этого времени.
"""

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import urllib3

from log.log import log

from ..channel_utils import is_dev_update_channel, normalize_update_channel
from ..network_hints import maybe_log_disable_dpi_for_update
from ..release_contract import ReleaseArtifactMetadata
from ..server_config import VPS_SERVERS, should_verify_ssl
from ..versions import normalize_version, version_key
from .http import MIRROR_TIMEOUT, Outcome, new_session, short_error


MIRRORS_DEADLINE_SECONDS = 20.0
# Сколько ждать остальные зеркала после первого корректного ответа.
MIRRORS_SETTLE_SECONDS = 1.5

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


def _ask(server: dict[str, Any], channel: str) -> dict[str, Any]:
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


def fetch_latest_release(
    channel: str,
    *,
    servers: list[dict[str, Any]] | None = None,
    timeout: float = MIRRORS_DEADLINE_SECONDS,
    settle: float = MIRRORS_SETTLE_SECONDS,
) -> dict[str, Any]:
    """Самый новый выпуск канала среди ответивших зеркал."""
    selected = servers if servers is not None else mirror_servers()
    if not selected:
        raise MirrorReleaseError("зеркала не настроены")
    answers: queue.Queue[tuple[int, Outcome[dict[str, Any]]]] = queue.Queue()

    def run(index: int, server: dict[str, Any]) -> None:
        try:
            answers.put((index, Outcome(value=_ask(server, channel))))
        except BaseException as exc:  # noqa: BLE001 — итог передаётся ждущему
            answers.put((index, Outcome(error=exc)))

    for index, server in enumerate(selected):
        threading.Thread(target=run, args=(index, server), name=f"update-mirror-{index}", daemon=True).start()

    deadline = time.monotonic() + float(timeout)
    releases: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    for _ in selected:
        try:
            index, outcome = answers.get(timeout=max(deadline - time.monotonic(), 0.0))
        except queue.Empty:
            break
        if not outcome.ok:
            errors.append(str(outcome.error))
            continue
        if not releases:
            deadline = min(deadline, time.monotonic() + float(settle))
        releases[index] = outcome.value or {}

    if not releases:
        raise MirrorReleaseError("; ".join(errors) or "нет ответа за отведённое время")
    # При равных версиях — зеркало, стоящее раньше в конфигурации сборки.
    _index, release = max(releases.items(), key=lambda item: (version_key(item[1]["version"]), -item[0]))
    log(f"✅ Зеркало {release['source']}: выпуск {release['version']}", "🔄 RELEASE")
    return release


@dataclass(frozen=True, slots=True)
class MirrorDownload:
    """Адреса одного файла на одном зеркале: основной HTTPS и запасной HTTP."""

    name: str
    host: str
    https_url: str
    http_url: str
    verify_ssl: bool


def download_sources(file_name: str) -> list[MirrorDownload]:
    """Адреса файла выпуска на каждом зеркале, в порядке из конфигурации сборки."""
    return [
        MirrorDownload(
            name=str(server.get("name") or server["host"]),
            host=str(server["host"]),
            https_url=f"https://{server['host']}:{server['https_port']}/download/{file_name}",
            http_url=f"http://{server['host']}:{server['http_port']}/download/{file_name}",
            verify_ssl=bool(should_verify_ssl()),
        )
        for server in VPS_SERVERS
    ]


__all__ = [
    "MIRRORS_DEADLINE_SECONDS",
    "MIRRORS_SETTLE_SECONDS",
    "MirrorDownload",
    "MirrorReleaseError",
    "download_sources",
    "fetch_latest_release",
    "fetch_versions",
    "mirror_servers",
    "release_from_versions",
]
