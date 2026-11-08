"""Подписанный список выпуска Zapret 2 GUI: откуда взять и чем проверить.

Сборка кладёт рядом с установщиком ``update-manifest.json`` и подпись
(Forgejo — файлами выпуска, зеркала — рядом с установщиком). Программа
скачивает оба файла с того же источника, что и данные выпуска, и проверяет
подпись открытыми ключами ниже. Контрольная сумма и размер установщика
берутся только из проверенного списка: зеркало, отдавшее подменённый файл
вместе с «подходящей» суммой, не пройдёт.

Ключи — рабочий и запасной (``zapret_update.manifest``). Закрытые части лежат
на машине сборки и у владельца, в git их нет.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from zapret_update.manifest import MAX_MANIFEST_BYTES, Manifest, ManifestError, load_signed

from .http import FORGEJO_TIMEOUT, new_session, short_error

PRODUCT = "zapret-gui"
TRUSTED_KEYS: dict[str, bytes] = {
    "gui-2026a": base64.b64decode("EHFnPsESeiNIq28c+jcvmqeClxDTPheSo/NgR+ea+kI="),
    "gui-reserve-1": base64.b64decode("c6fNXZOtY5he05hDoxEx9Y7Zkcej0F0MaeE9tekbn1s="),
}
MAX_SIGNATURE_BYTES = 1024


@dataclass(frozen=True)
class SignedRelease:
    """Проверенный список выпуска вместе с исходными байтами и подписью.

    Байты и подпись программа кладёт к себе после установки — это список
    установленной версии: по нему следующее обновление знает, какие файлы
    поставила сама программа.
    """

    manifest: Manifest
    raw: bytes
    signature: bytes


def _download(session, url: str, *, verify: bool, limit: int) -> bytes:
    response = session.get(url, timeout=FORGEJO_TIMEOUT, verify=verify, stream=True)
    try:
        response.raise_for_status()
        chunks, total = [], 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ManifestError("файл списка выпуска слишком велик", "format")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        response.close()


def fetch_verified_manifest(
    release: Mapping[str, Any],
    *,
    channel: str,
    current_version: str,
    allow_same: bool = False,
    session_factory=new_session,
) -> Manifest:
    """Проверенный список выпуска, который назвал источник; иначе ``ManifestError``."""
    return fetch_signed_release(
        release, channel=channel, current_version=current_version, allow_same=allow_same, session_factory=session_factory
    ).manifest


def fetch_signed_release(
    release: Mapping[str, Any],
    *,
    channel: str,
    current_version: str,
    allow_same: bool = False,
    session_factory=new_session,
) -> SignedRelease:
    """Как ``fetch_verified_manifest``, но вместе с байтами списка и подписью.

    ``release["signed_blob"]`` — список и подпись, уже полученные от соседей по интернету
    (``updater.share.wiring.release_from_peers``): скачивать нечего, а проверка ниже та же.
    """
    blob = release.get("signed_blob")
    if blob:
        raw, signature = bytes(blob[0]), bytes(blob[1])
    else:
        manifest_url = str(release.get("manifest_url") or "")
        signature_url = str(release.get("signature_url") or "")
        if not manifest_url or not signature_url:
            raise ManifestError("источник не отдал подписанный список выпуска", "nosig")
        verify = bool(release.get("verify_ssl", True))
        session = session_factory()
        try:
            try:
                raw = _download(session, manifest_url, verify=verify, limit=MAX_MANIFEST_BYTES)
                signature = _download(session, signature_url, verify=verify, limit=MAX_SIGNATURE_BYTES)
            except ManifestError:
                raise
            except Exception as exc:
                raise ManifestError(f"список выпуска не скачался — {short_error(exc)}", "nosig") from exc
        finally:
            session.close()
    manifest = load_signed(
        raw,
        signature,
        trusted=TRUSTED_KEYS,
        product=PRODUCT,
        channel=channel,
        newer_than=current_version,
        allow_same=allow_same,
    )
    if str(release.get("version") or "") != manifest.version:
        raise ManifestError(
            f"источник назвал версию {release.get('version')}, а подписанный список — {manifest.version}", "sign"
        )
    if manifest.installer is None:
        raise ManifestError("в подписанном списке выпуска нет установщика", "format")
    return SignedRelease(manifest, bytes(raw), bytes(signature))


__all__ = ["PRODUCT", "TRUSTED_KEYS", "SignedRelease", "fetch_signed_release", "fetch_verified_manifest"]
