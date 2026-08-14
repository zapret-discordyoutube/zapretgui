"""Ядро синхронизации удалённых пресетов.

Модуль без Qt: сеть, чтение и запись пресета передаются callables,
поэтому логика полностью покрывается unit-тестами. Вызывается из
фонового автосинка (подсистемная очередь "presets") и из ручного
«Обновить из источника» (QThread-воркер).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from presets.preset_text_ops import validate_preset_source_text
from updater.proxy_bypass import request_get_bypass_proxy
from updater.server_config import CONNECT_TIMEOUT, READ_TIMEOUT

MAX_REMOTE_PRESET_BYTES = 256 * 1024
AUTO_CHECK_INTERVAL_SECONDS = 6 * 3600
MANUAL_CHECK_THROTTLE_SECONDS = 15
_USER_AGENT = "Zapret-RemotePreset/1.0"

STATUS_UPDATED = "updated"
STATUS_UNCHANGED = "unchanged"
STATUS_NOT_MODIFIED = "not_modified"
STATUS_DETACHED = "detached"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"


@dataclass(frozen=True, slots=True)
class RemoteFetchResult:
    status_code: int
    text: str = ""
    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True, slots=True)
class RemoteSyncOutcome:
    status: str
    detail: str = ""
    binding_updates: dict = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_ts(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def comparison_hash(source_text: str) -> str:
    """Хэш «сравнительной формы» пресета.

    Комментарии (#) и строка --debug= исключаются: rename переписывает
    заголовок `# Preset:`, а тумблер debug-лога вставляет `--debug=` —
    это действия приложения, а не правки пользователя, и они не должны
    отвязывать пресет от источника.
    """
    text = str(source_text or "").replace("\r\n", "\n").replace("\r", "\n")
    meaningful: list[str] = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("--debug="):
            continue
        meaningful.append(stripped)
    payload = "\n".join(meaningful).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def fetch_remote_preset_text(url: str, *, etag: str = "", last_modified: str = "") -> RemoteFetchResult:
    """Скачивает текст пресета; https-only, условные запросы, лимит размера.

    Ошибки сети/HTTP/размера поднимаются как RemoteSyncFetchError.
    """
    source_url = str(url or "").strip()
    if urlsplit(source_url).scheme.lower() != "https":
        raise RemoteSyncFetchError("Автообновление работает только по https")

    headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    try:
        response = request_get_bypass_proxy(
            source_url,
            headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            verify=True,
            stream=True,
        )
    except requests.exceptions.RequestException as exc:
        raise RemoteSyncFetchError(f"Сеть: {exc}") from exc

    try:
        if int(response.status_code) == 304:
            return RemoteFetchResult(status_code=304)
        if int(response.status_code) >= 400:
            raise RemoteSyncFetchError(f"HTTP {response.status_code}")

        total = 0
        chunks: list[bytes] = []
        try:
            for chunk in response.iter_content(chunk_size=16 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_REMOTE_PRESET_BYTES:
                    raise RemoteSyncFetchError(
                        f"Файл больше {MAX_REMOTE_PRESET_BYTES // 1024} КБ"
                    )
                chunks.append(chunk)
        except requests.exceptions.RequestException as exc:
            raise RemoteSyncFetchError(f"Сеть: {exc}") from exc

        payload = b"".join(chunks)
        if payload.startswith(b"PK\x03\x04"):
            raise RemoteSyncFetchError("Архивы не автообновляются")
        text = payload.decode("utf-8", errors="replace")
        return RemoteFetchResult(
            status_code=int(response.status_code),
            text=text,
            etag=str(response.headers.get("ETag", "") or ""),
            last_modified=str(response.headers.get("Last-Modified", "") or ""),
        )
    finally:
        response.close()


class RemoteSyncFetchError(Exception):
    pass


def should_auto_check(binding: dict, *, now_ts: float, parse_ts) -> bool:
    """Пора ли автопроверять пресет: auto включён, не detached, интервал прошёл.

    parse_ts(iso_string) -> float | None — конвертер ISO-времени в epoch
    (инжектируется, чтобы ядро не зависело от формата хранения).
    """
    if not isinstance(binding, dict):
        return False
    if not bool(binding.get("auto", True)) or bool(binding.get("detached", False)):
        return False
    checked_at = str(binding.get("checked_at") or "")
    if not checked_at:
        return True
    checked_ts = parse_ts(checked_at)
    if checked_ts is None:
        return True
    return (float(now_ts) - float(checked_ts)) >= AUTO_CHECK_INTERVAL_SECONDS


def sync_remote_preset(
    binding: dict,
    *,
    engine: str,
    read_current_text,
    fetch,
    save_text,
    now_iso: str,
    force: bool = False,
) -> RemoteSyncOutcome:
    """Одна синхронизация одного пресета.

    read_current_text() -> str | None — текущий текст файла (None = файла нет);
    fetch(url, etag, last_modified) -> RemoteFetchResult (может бросить
    RemoteSyncFetchError); save_text(text) — запись через штатный pipeline
    (для активного пресета он сам перезапустит стратегию);
    force=True — ручное обновление: игнорирует detached и перезаписывает
    локальные правки.
    """
    url = str((binding or {}).get("url") or "").strip()
    if not url:
        return RemoteSyncOutcome(status=STATUS_ERROR, detail="Привязка без URL")

    current_text = read_current_text()
    if current_text is None:
        return RemoteSyncOutcome(status=STATUS_ERROR, detail="Файл пресета не найден")

    synced_hash = str(binding.get("synced_hash") or "")
    local_changed = bool(synced_hash) and comparison_hash(current_text) != synced_hash
    if local_changed and not force:
        if bool(binding.get("detached", False)):
            return RemoteSyncOutcome(status=STATUS_SKIPPED, detail="Автосинк приостановлен")
        return RemoteSyncOutcome(
            status=STATUS_DETACHED,
            detail="Пресет изменён локально — автообновление приостановлено",
            binding_updates={"detached": True, "checked_at": now_iso},
        )

    try:
        # force-режим не шлёт условные заголовки: пользователь явно просит
        # перекачать контент, даже если источник не менялся.
        fetched = fetch(
            url,
            "" if force else str(binding.get("etag") or ""),
            "" if force else str(binding.get("last_modified") or ""),
        )
    except RemoteSyncFetchError as exc:
        return RemoteSyncOutcome(
            status=STATUS_ERROR,
            detail=str(exc),
            binding_updates={"error": str(exc), "checked_at": now_iso},
        )

    if fetched.status_code == 304:
        return RemoteSyncOutcome(
            status=STATUS_NOT_MODIFIED,
            binding_updates={"error": "", "checked_at": now_iso},
        )

    validation_error = validate_preset_source_text(fetched.text, engine=engine)
    if validation_error:
        detail = f"Файл по ссылке не похож на пресет: {validation_error}"
        return RemoteSyncOutcome(
            status=STATUS_ERROR,
            detail=detail,
            binding_updates={"error": detail, "checked_at": now_iso},
        )

    remote_hash = comparison_hash(fetched.text)
    common_updates = {
        "error": "",
        "checked_at": now_iso,
        "etag": fetched.etag,
        "last_modified": fetched.last_modified,
    }
    if remote_hash == comparison_hash(current_text):
        return RemoteSyncOutcome(
            status=STATUS_UNCHANGED,
            binding_updates={
                **common_updates,
                "synced_hash": remote_hash,
                "detached": False if force else bool(binding.get("detached", False)),
            },
        )

    saved_text = save_text(fetched.text)
    # Запись могла нормализовать текст (normalize_source_text) — хэш считаем
    # от фактически сохранённого содержимого, иначе следующая проверка
    # ложно решит, что пользователь правил файл, и отвяжет пресет.
    if isinstance(saved_text, str) and saved_text:
        remote_hash = comparison_hash(saved_text)
    return RemoteSyncOutcome(
        status=STATUS_UPDATED,
        binding_updates={
            **common_updates,
            "synced_hash": remote_hash,
            "updated_at": now_iso,
            "detached": False,
        },
    )
