"""Поиск актуального GoogleVideo CDN через сеть текущего пользователя.

YouTube выбирает видеосерверы по провайдеру, региону и маршруту. Поэтому
``rr*...googlevideo.com`` нельзя хранить во встроенном списке BlockCheck:
адрес, подходящий одному пользователю сегодня, у другого уже не существует.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import html
import re
import time
from urllib.parse import unquote

from blockcheck.hosts import host_of


_DISCOVERY_TIMEOUT_SECONDS = 8.0
_MAX_WATCH_PAGE_BYTES = 2_000_000
_WATCH_URLS: tuple[str, ...] = (
    "https://www.youtube.com/watch?v=jNQXAC9IVRw&hl=en",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&hl=en",
    "https://www.youtube.com/watch?v=Qr1zDbHATw0&t=8s&hl=en",
)
_GOOGLEVIDEO_RR_RE = re.compile(
    r"(?i)\b((?:rr|r)\d+(?:---|\.)sn-[a-z0-9-]+\.googlevideo\.com)\b"
)


@dataclass(frozen=True, slots=True)
class GoogleVideoDiscoveryResult:
    host: str | None = None
    candidates: tuple[str, ...] = ()
    source_url: str = ""
    detail: str = ""


# Apex-домен не является видеосервером: прямая проверка всегда падает,
# работают только rr*-поддомены или служебный redirector.
_BARE_GOOGLEVIDEO_HOSTS = frozenset({"googlevideo.com", "www.googlevideo.com"})


def is_bare_googlevideo_host(value: str | None) -> bool:
    """True для голого googlevideo.com в любой записи (URL, порт, регистр)."""
    return host_of(str(value or "")) in _BARE_GOOGLEVIDEO_HOSTS


def normalize_googlevideo_host(value: str | None) -> str:
    """Возвращает только настоящее имя видеосервера ``rr/r...googlevideo``."""
    host = str(value or "").strip().lower().rstrip(".")
    if not _GOOGLEVIDEO_RR_RE.fullmatch(host):
        return ""
    return host


def _extract_googlevideo_hosts(page: str) -> tuple[str, ...]:
    """Извлекает CDN-имена из обычных и URL-кодированных данных YouTube."""
    decoded = html.unescape(str(page or ""))
    for _ in range(3):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted

    hosts: list[str] = []
    seen: set[str] = set()
    for match in _GOOGLEVIDEO_RR_RE.finditer(decoded):
        host = normalize_googlevideo_host(match.group(1))
        if not host or host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return tuple(hosts)


def _fetch_watch_page(
    url: str,
    timeout: float,
    cancelled: Callable[[], bool],
) -> str:
    """Загружает не больше 2 МБ страницы и останавливается после stream URL."""
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
    }
    payload = bytearray()
    with requests.Session() as client:
        client.max_redirects = 5
        with client.get(
            url,
            timeout=timeout,
            verify=True,
            allow_redirects=True,
            headers=headers,
            stream=True,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=32 * 1024):
                if cancelled():
                    return ""
                remaining = _MAX_WATCH_PAGE_BYTES - len(payload)
                if remaining <= 0:
                    break
                payload.extend(chunk[:remaining])

                # На типичной странице первый stream URL появляется примерно
                # после 600-800 КБ. Не скачиваем оставшуюся разметку без нужды.
                if b"googlevideo" in payload.lower():
                    page = payload.decode("utf-8", errors="ignore")
                    if _extract_googlevideo_hosts(page):
                        return page

    return payload.decode("utf-8", errors="ignore")


def discover_googlevideo_host(
    *,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = _DISCOVERY_TIMEOUT_SECONDS,
) -> GoogleVideoDiscoveryResult:
    """Получает свежий CDN-хост из ответа YouTube на этой машине и в этой сети.

    Результат намеренно не кэшируется: новый запуск BlockCheck снова спрашивает
    YouTube, потому что выбранный CDN может измениться вместе с сетью или
    маршрутом пользователя.
    """
    is_cancelled = cancelled or (lambda: False)
    if is_cancelled():
        return GoogleVideoDiscoveryResult(detail="проверка остановлена")

    deadline = time.monotonic() + max(0.1, float(timeout))
    errors: list[str] = []
    for url in _WATCH_URLS:
        if is_cancelled():
            return GoogleVideoDiscoveryResult(detail="проверка остановлена")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            errors.append("превышен лимит времени")
            break
        try:
            page = _fetch_watch_page(url, max(0.1, remaining), is_cancelled)
        except ImportError:
            return GoogleVideoDiscoveryResult(detail="компонент requests недоступен")
        except Exception as exc:  # сбой одной страницы не отменяет вторую попытку
            errors.append(str(exc).strip()[:120] or type(exc).__name__)
            continue

        hosts = _extract_googlevideo_hosts(page)
        if hosts:
            return GoogleVideoDiscoveryResult(
                host=hosts[0],
                candidates=hosts,
                source_url=url,
                detail=f"найдено вариантов: {len(hosts)}",
            )

    if is_cancelled():
        return GoogleVideoDiscoveryResult(detail="проверка остановлена")
    if errors:
        return GoogleVideoDiscoveryResult(
            detail=f"YouTube не отдал список видеосерверов: {errors[-1]}"
        )
    return GoogleVideoDiscoveryResult(
        detail="YouTube не отдал ни одного адреса видеосервера"
    )
