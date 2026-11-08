"""Подписанный список выпуска от соседей: когда сервер обновлений не отвечает совсем.

Если заблокирована и сама проверка новой версии, программа не узнает о выпуске, и
запасной путь за файлами был бы бесполезен. Поэтому соседи отдают ещё и подписанный
список своей версии (``GET /zapret-share/1/manifest``). Верить соседу не нужно:

- список принимается, только если подпись сошлась с одним из ключей, зашитых в
  программу, продукт и канал свои, версия строго новее установленной;
- список не старше ``MAX_AGE_DAYS`` со времени сборки: старый подлинный выпуск
  (например, отозванный позже) нельзя подсунуть вместо нового;
- из ответов нескольких соседей берётся самая новая версия.

Остаточный риск, о котором стоит знать: сосед может промолчать о новом выпуске или
отдать не самый свежий из подлинных. Подделать выпуск он не может.

Только стандартная библиотека.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import ssl
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from .manifest import MAX_MANIFEST_BYTES, Manifest, ManifestError, compare_versions, load_signed

MANIFEST_ROUTE = "/zapret-share/1/manifest"
MAX_AGE_DAYS = 45
MAX_REPLY = (MAX_MANIFEST_BYTES * 4) // 3 + 4096
ASK_LIMIT = 6
TIMEOUT = 4.0


@dataclass(frozen=True, slots=True)
class PeerRelease:
    manifest: Manifest
    raw: bytes
    signature: bytes
    source: str


def encode_manifest_reply(raw: bytes, signature: bytes) -> bytes:
    body = {
        "manifest": base64.b64encode(bytes(raw)).decode("ascii"),
        "signature": base64.b64encode(bytes(signature)).decode("ascii"),
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def decode_manifest_reply(data: bytes) -> tuple[bytes, bytes] | None:
    if len(data) > MAX_REPLY:
        return None
    try:
        body = json.loads(data.decode("utf-8"))
        raw = base64.b64decode(body["manifest"], validate=True)
        signature = base64.b64decode(body["signature"], validate=True)
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, binascii.Error):
        return None
    if not raw or len(raw) > MAX_MANIFEST_BYTES or not signature or len(signature) > 1024:
        return None
    return raw, signature


def _ask(neighbour, timeout: float) -> tuple[bytes, bytes] | None:
    if getattr(neighbour, "tls", False):
        from .share_cert import client_context

        connection = http.client.HTTPSConnection(neighbour.host, neighbour.port, timeout=timeout, context=client_context())
    else:
        connection = http.client.HTTPConnection(neighbour.host, neighbour.port, timeout=timeout)
    try:
        connection.request("GET", MANIFEST_ROUTE, headers={"User-Agent": "ZapretShare/1"})
        reply = connection.getresponse()
        if reply.status != 200:
            return None
        return decode_manifest_reply(reply.read(MAX_REPLY + 1))
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return None
    finally:
        connection.close()


def fetch_from_peers(
    neighbours: Iterable,
    *,
    trusted: Mapping[str, bytes],
    product: str,
    channel: str,
    current_version: str,
    max_age_days: float = MAX_AGE_DAYS,
    now: Callable[[], float] = time.time,
    timeout: float = TIMEOUT,
    limit: int = ASK_LIMIT,
    ask: Callable = _ask,
) -> PeerRelease | None:
    """Самый новый подлинный список выпуска у соседей или ``None``."""
    best: PeerRelease | None = None
    for neighbour in list(neighbours)[: max(int(limit), 0)]:
        try:
            answer = ask(neighbour, timeout)
        except Exception:  # noqa: BLE001
            answer = None
        if answer is None:
            continue
        raw, signature = answer
        try:
            manifest = load_signed(
                raw, signature, trusted=trusted, product=product, channel=channel, newer_than=current_version
            )
        except (ManifestError, ValueError):
            continue
        if manifest.created < now() - max_age_days * 86400:
            continue
        if best is None or compare_versions(manifest.version, best.manifest.version) > 0:
            best = PeerRelease(manifest, bytes(raw), bytes(signature), f"{neighbour.host}:{neighbour.port}")
    return best


__all__ = [
    "MANIFEST_ROUTE", "MAX_AGE_DAYS", "PeerRelease", "decode_manifest_reply", "encode_manifest_reply", "fetch_from_peers",
]
