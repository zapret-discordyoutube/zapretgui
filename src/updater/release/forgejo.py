from __future__ import annotations

"""Выпуски ZapretGUI из собственного Forgejo — главный источник обновления.

Forgejo хранит установщик и отдельный файл ``<installer>.sha256`` в одном
выпуске. Клиент принимает только точные ссылки собственного домена и только
пару из этих двух файлов. Так контрольная сумма не зависит от необязательных
полей API и проверяет уже скачанные байты установщика.

Выбор выпуска:

* кандидаты отбираются только по списку выпусков, без скачиваний: не
  черновик, подходит канал, версия разбирается, в выпуске есть и ``.exe``, и
  его ``.sha256``. Выпуск, который ещё выгружается, просто не кандидат;
* лучший кандидат — старший по номеру версии, а не по дате: заново
  опубликованный старый выпуск не должен считаться новейшим;
* файл суммы скачивается только у лучшего кандидата. Не скачался — это
  ошибка проверки, а не повод молча предложить предыдущий выпуск.

Никаких кэшей: каждый вызов — свежий ответ Forgejo.
"""

import re
import time
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import requests

from log.log import log
from utils.https_dns_fallback import request_with_dns_fallback

from ..channel_utils import is_dev_release_asset_name, normalize_update_channel
from ..network_hints import maybe_log_disable_dpi_for_update
from ..versions import normalize_version, version_key
from .http import FORGEJO_TIMEOUT, new_session, short_error


FORGEJO_ORIGIN = "https://git.zapret.moe"
FORGEJO_HOST = "git.zapret.moe"
FORGEJO_REPOSITORY = "zapretdiscordyoutube/zapretgui"
FORGEJO_API_URL = f"{FORGEJO_ORIGIN}/api/v1/repos/{FORGEJO_REPOSITORY}/releases"
FORGEJO_REPOSITORY_API_URL = f"{FORGEJO_ORIGIN}/api/v1/repos/{FORGEJO_REPOSITORY}"
FORGEJO_RELEASE_DOWNLOAD_PREFIX = f"/{FORGEJO_REPOSITORY}/releases/download/"
FORGEJO_RELEASE_PAGE_PREFIX = f"{FORGEJO_ORIGIN}/{FORGEJO_REPOSITORY}/releases/tag/"
FORGEJO_SOURCE = "Forgejo"
RELEASES_PAGE_LIMIT = 50
MAX_RELEASE_PAGES = 4
MAX_SIDECAR_BYTES = 4096

_SHA256_LINE_RE = re.compile(r"\A([0-9a-fA-F]{64})  ([^\r\n]+)\r?\n?\Z")


class ForgejoReleaseError(RuntimeError):
    """Forgejo не дал проверенного выпуска нужного канала."""


def _trusted_release_asset_url(url: str, *, tag_name: str, file_name: str) -> bool:
    try:
        parsed = urlsplit(str(url or ""))
    except Exception:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname != FORGEJO_HOST
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


def release_page_url(tag_name: str) -> str:
    """Страница выпуска на Forgejo — для кнопки «Открыть в браузере»."""
    return f"{FORGEJO_RELEASE_PAGE_PREFIX}{quote(str(tag_name or ''), safe='')}"


def _parse_sha256_sidecar(text: str, *, expected_name: str) -> str:
    match = _SHA256_LINE_RE.fullmatch(str(text or ""))
    if not match or match.group(2) != expected_name:
        raise ValueError("Некорректный файл контрольной суммы выпуска")
    return match.group(1).lower()


def _get(session: requests.Session, url: str, *, accept: str) -> requests.Response:
    response = request_with_dns_fallback(
        session,
        "GET",
        url,
        headers={"Accept": accept},
        timeout=FORGEJO_TIMEOUT,
        verify=True,
    )
    response.raise_for_status()
    return response


def _matches_channel(release: dict[str, Any], exe_name: str, channel: str) -> bool:
    is_dev = bool(release.get("prerelease")) or is_dev_release_asset_name(exe_name)
    return is_dev if channel == "dev" else not is_dev


def _candidate(release: object, channel: str) -> dict[str, Any] | None:
    """Кандидат из списка выпусков без единого скачивания, либо None."""
    if not isinstance(release, dict) or release.get("draft"):
        return None
    tag_name = str(release.get("tag_name") or "")
    try:
        version = normalize_version(tag_name)
    except ValueError:
        return None

    assets = [asset for asset in release.get("assets") or () if isinstance(asset, dict)]
    exe_asset = next(
        (asset for asset in assets if str(asset.get("name") or "").lower().endswith(".exe")),
        None,
    )
    if exe_asset is None:
        return None
    exe_name = str(exe_asset.get("name") or "")
    if not _matches_channel(release, exe_name, channel):
        return None

    sidecar_name = f"{exe_name}.sha256"
    sidecar_asset = next((asset for asset in assets if asset.get("name") == sidecar_name), None)
    if sidecar_asset is None:
        return None

    exe_url = str(exe_asset.get("browser_download_url") or "")
    sidecar_url = str(sidecar_asset.get("browser_download_url") or "")
    if not _trusted_release_asset_url(exe_url, tag_name=tag_name, file_name=exe_name):
        return None
    if not _trusted_release_asset_url(sidecar_url, tag_name=tag_name, file_name=sidecar_name):
        return None

    return {
        "version": version,
        "tag_name": tag_name,
        "update_url": exe_url,
        "file_name": exe_name,
        "file_size": exe_asset.get("size"),
        "sidecar_url": sidecar_url,
        "release_notes": str(release.get("body") or ""),
        "prerelease": bool(release.get("prerelease", False)),
        "name": str(release.get("name") or ""),
        "published_at": str(release.get("published_at") or ""),
        "release_url": release_page_url(tag_name),
    }


def _list_candidates(session: requests.Session, channel: str) -> list[dict[str, Any]]:
    """Кандидаты канала. Листаем, пока не встретится хотя бы один.

    Forgejo отдаёт выпуски от новых к старым, поэтому новейший выпуск канала
    находится на первой странице, где канал вообще встречается. Листать дальше
    нужно, если после последнего Stable вышло больше страницы Dev-выпусков.
    """
    candidates: list[dict[str, Any]] = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        payload = _get(
            session,
            f"{FORGEJO_API_URL}?limit={RELEASES_PAGE_LIMIT}&page={page}",
            accept="application/json",
        ).json()
        if not isinstance(payload, list):
            raise ForgejoReleaseError("Forgejo вернул неожиданный ответ вместо списка выпусков")
        for release in payload:
            candidate = _candidate(release, channel)
            if candidate is not None:
                candidates.append(candidate)
        if candidates or len(payload) < RELEASES_PAGE_LIMIT:
            break
    return candidates


def _fetch_sha256(session: requests.Session, candidate: dict[str, Any]) -> str:
    response = _get(session, str(candidate["sidecar_url"]), accept="text/plain")
    content = response.content
    if len(content) > MAX_SIDECAR_BYTES:
        raise ValueError("Файл контрольной суммы слишком большой")
    return _parse_sha256_sidecar(content.decode("ascii"), expected_name=str(candidate["file_name"]))


def fetch_latest_release(channel: str) -> dict[str, Any]:
    """Новейший проверенный выпуск канала. ForgejoReleaseError, если его нет."""
    selected = normalize_update_channel(channel)
    started = time.monotonic()
    session = new_session()
    try:
        try:
            candidates = _list_candidates(session, selected)
        except ForgejoReleaseError:
            raise
        except Exception as exc:
            maybe_log_disable_dpi_for_update(exc, scope="update_check", level="🔄 RELEASE")
            raise ForgejoReleaseError(f"список выпусков недоступен — {short_error(exc)}") from exc
        if not candidates:
            raise ForgejoReleaseError(f"нет готовых выпусков канала {selected}")

        best = max(candidates, key=lambda item: version_key(item["version"]))
        try:
            sha256 = _fetch_sha256(session, best)
        except Exception as exc:
            raise ForgejoReleaseError(
                f"не удалось получить контрольную сумму выпуска {best['version']} — {short_error(exc)}"
            ) from exc
    finally:
        session.close()

    release = {key: value for key, value in best.items() if key != "sidecar_url"}
    release["sha256"] = sha256
    # Список выпусков канала уже на руках: из него окно обновления показывает
    # изменения всех пропущенных версий, без отдельного запроса.
    release["history"] = tuple(_history_entry(item) for item in candidates)
    release["source"] = FORGEJO_SOURCE
    release["verify_ssl"] = True
    log(
        f"✅ Forgejo: выпуск {release['version']} ({time.monotonic() - started:.2f} с)",
        "🔄 RELEASE",
    )
    return release


def _history_entry(candidate: dict[str, Any]) -> dict[str, str]:
    return {
        "version": str(candidate.get("version") or ""),
        "notes": str(candidate.get("release_notes") or ""),
        "published_at": str(candidate.get("published_at") or ""),
        "url": str(candidate.get("release_url") or ""),
    }


def fetch_release_notes(version: str) -> dict[str, str]:
    """Текст выпуска одной версии — для «Что нового» в уже установленной программе.

    ForgejoReleaseError, если выпуска нет или Forgejo недоступен.
    """
    tag_name = normalize_version(version)
    session = new_session()
    try:
        try:
            payload = _get(
                session,
                f"{FORGEJO_API_URL}/tags/{quote(tag_name, safe='')}",
                accept="application/json",
            ).json()
        except Exception as exc:
            raise ForgejoReleaseError(f"выпуск {tag_name} недоступен — {short_error(exc)}") from exc
    finally:
        session.close()
    if not isinstance(payload, dict) or payload.get("draft"):
        raise ForgejoReleaseError(f"Forgejo не вернул выпуск {tag_name}")
    return {
        "version": tag_name,
        "notes": str(payload.get("body") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "url": release_page_url(tag_name),
    }


def fetch_recent_release_history(channel: str, *, up_to_version: str) -> tuple[dict[str, str], ...]:
    """Выпуски канала не новее ``up_to_version`` — для «Что нового» без сохранённого текста.

    Один запрос списка, без файлов суммы. ForgejoReleaseError, если Forgejo
    недоступен или выпусков нет.
    """
    selected = normalize_update_channel(channel)
    session = new_session()
    try:
        try:
            candidates = _list_candidates(session, selected)
        except ForgejoReleaseError:
            raise
        except Exception as exc:
            raise ForgejoReleaseError(f"список выпусков недоступен — {short_error(exc)}") from exc
    finally:
        session.close()
    if not candidates:
        raise ForgejoReleaseError(f"нет выпусков канала {selected}")
    return tuple(_history_entry(item) for item in candidates)


def probe_forgejo() -> float:
    """Свежая проверка доступности API Forgejo. Время ответа в секундах."""
    started = time.monotonic()
    session = new_session()
    try:
        data = _get(session, FORGEJO_REPOSITORY_API_URL, accept="application/json").json()
    finally:
        session.close()
    if not isinstance(data, dict) or data.get("full_name") != FORGEJO_REPOSITORY:
        raise ForgejoReleaseError("Forgejo API не вернул ожидаемый репозиторий")
    return time.monotonic() - started


__all__ = [
    "FORGEJO_API_URL",
    "FORGEJO_SOURCE",
    "ForgejoReleaseError",
    "fetch_latest_release",
    "fetch_recent_release_history",
    "fetch_release_notes",
    "probe_forgejo",
    "release_page_url",
]
