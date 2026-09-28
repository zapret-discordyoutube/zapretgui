"""Обрыв на 16–20 КБ: режет ли провайдер загрузку с зарубежных серверов.

ТСПУ пропускает начало ответа, а после ~16 КБ соединение замирает или
рвётся. Проверка качает по ~32 КБ с нескольких хостингов разных провайдеров:
если у одних файл приходит целиком, а у других обрывается на 14–24 КБ —
это и есть такой обрыв.

Что было неверно раньше:

* обрыв засчитывался, только если в тексте ошибки было слово «reset», а
  самый частый случай — соединение просто замирает (таймаут чтения) — уходил в
  «ошибку» и в итоге превращался в «доступ есть»;
* сервер, который сразу сбросил соединение (0 байт), тоже давал «доступ есть».
  На деле это «не удалось проверить»: сброс сразу — другой вид блокировки или
  недоступный хостинг, про 16 КБ он ничего не говорит.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from diagnostics.tls_probe import ProbeResult
from diagnostics.verdict import FREEZE_MAX_BYTES, FREEZE_MIN_BYTES, Level, describe_reach

__all__ = [
    "FreezeReport",
    "FreezeServer",
    "FreezeState",
    "check_freeze",
    "classify_download",
    "pick_freeze_targets",
    "summarize_freeze",
]

READ_LIMIT = 32 * 1024
TARGETS_COUNT = 6
# Провайдеры в порядке предпочтения: крупные хостинги, у которых ТСПУ
# обрывает загрузку чаще всего.
_PREFERRED_PROVIDERS = ("Akamai", "AWS", "Cloudflare", "CDN77", "Hetzner", "OVH", "DigitalOcean", "Fastly")


class FreezeState(Enum):
    OK = "ok"
    FREEZE = "freeze"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FreezeServer:
    name: str
    state: FreezeState
    text: str


@dataclass(frozen=True, slots=True)
class FreezeReport:
    level: Level
    headline: str
    servers: tuple[FreezeServer, ...]
    advice: tuple[str, ...] = ()


def pick_freeze_targets(targets, count: int = TARGETS_COUNT) -> list[dict]:
    """По одному HTTPS-адресу от разных провайдеров, в постоянном порядке."""
    by_provider: dict[str, dict] = {}
    for item in targets:
        url = str(item.get("url") or "")
        provider = str(item.get("provider") or "")
        if not url.startswith("https://") or provider in by_provider:
            continue
        by_provider[provider] = item
    ordered = [by_provider[name] for name in _PREFERRED_PROVIDERS if name in by_provider]
    ordered += [item for name, item in by_provider.items() if name not in _PREFERRED_PROVIDERS]
    return ordered[: max(0, int(count))]


def classify_download(result: ProbeResult | None) -> tuple[FreezeState, str]:
    """Что значит результат загрузки для проверки обрыва."""
    if result is None:
        return FreezeState.UNKNOWN, "не удалось узнать адрес сервера"
    if not result.ok:
        return FreezeState.UNKNOWN, f"не удалось проверить: {describe_reach(result)}"
    size = int(result.body_size)
    kb = size // 1024
    if result.body_cut and FREEZE_MIN_BYTES <= size <= FREEZE_MAX_BYTES:
        return FreezeState.FREEZE, f"загрузка оборвалась на {kb} КБ"
    if size >= FREEZE_MAX_BYTES:
        return FreezeState.OK, f"получено {kb} КБ без обрыва"
    if result.body_cut:
        return FreezeState.UNKNOWN, f"загрузка оборвалась на {kb} КБ — не похоже на обрыв ТСПУ"
    status = int(result.status or 0)
    if status >= 300:
        return FreezeState.UNKNOWN, f"сервер не отдал файл (код {status})"
    return FreezeState.UNKNOWN, f"файл слишком маленький ({kb} КБ) — не показательно"


def check_freeze(
    submit: Callable[..., Future],
    wait: Callable[[Future], object],
    download: Callable[[str, str], ProbeResult | None],
) -> tuple[FreezeServer, ...]:
    """Качает файлы параллельно. ``download(host, path)`` сам выбирает адрес."""
    from blockcheck.data_lists import TCP_16_20_TARGETS

    planned = []
    for item in pick_freeze_targets(TCP_16_20_TARGETS):
        parts = urlsplit(str(item["url"]))
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        name = f"{item.get('provider', '')} ({parts.hostname})"
        planned.append((name, submit(download, parts.hostname or "", path)))

    servers: list[FreezeServer] = []
    for name, future in planned:
        try:
            state, text = classify_download(wait(future))
        except Exception as exc:
            state, text = FreezeState.UNKNOWN, f"ошибка проверки ({exc})"
        servers.append(FreezeServer(name=name, state=state, text=text))
    return tuple(servers)


def summarize_freeze(servers: tuple[FreezeServer, ...], *, zapret_running: bool | None) -> FreezeReport:
    frozen = [item for item in servers if item.state == FreezeState.FREEZE]
    fine = [item for item in servers if item.state == FreezeState.OK]
    if frozen:
        advice = (
            ("Подберите стратегию: в «Подборе стратегии» укажите сайт, который грузится не до конца.",)
            if zapret_running
            else ("Запустите Zapret на странице «Управление Zapret 2» — стратегии обходят этот обрыв.",)
        )
        return FreezeReport(
            Level.FAIL if len(frozen) >= len(fine) else Level.WARN,
            f"Провайдер обрывает загрузку с зарубежных серверов на 16–20 КБ ({len(frozen)} из {len(servers)})",
            servers,
            advice,
        )
    if fine:
        return FreezeReport(Level.OK, "Обрыва загрузки на 16–20 КБ нет", servers)
    return FreezeReport(
        Level.UNKNOWN,
        "Обрыв на 16–20 КБ проверить не удалось: тестовые серверы не ответили",
        servers,
    )
