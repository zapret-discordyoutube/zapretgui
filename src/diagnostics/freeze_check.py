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
* у каждого провайдера бралось по одному адресу: если он умер или не ответил,
  провайдер выпадал из проверки, а итог всё равно был «обрыва нет», хотя
  проверилась половина серверов. Теперь при неудаче берётся запасной адрес того
  же провайдера, а итог честно говорит, сколько серверов проверено.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from diagnostics.tls_probe import ProbeResult
from diagnostics.upload_probe import UPLOAD_BULK_STALLS, UPLOAD_OK, UPLOAD_PACKET_LIMIT, UploadVerdict
from diagnostics.verdict import FREEZE_MAX_BYTES, FREEZE_MIN_BYTES, Level

__all__ = [
    "DIRECTION_DOWNLOAD",
    "DIRECTION_UPLOAD",
    "FreezeReport",
    "FreezeServer",
    "FreezeState",
    "check_freeze",
    "classify_download",
    "every_freeze_target",
    "pick_freeze_targets",
    "summarize_freeze",
]

READ_LIMIT = 32 * 1024
TARGETS_COUNT = 6
# Сколько серверов полной проверки опрашивать одновременно.
EVERY_AT_ONCE = 12
# Один обрыв при стольких серверах без обрыва — единичный случай, а не вывод о сети.
SINGLE_CUT_NEEDS_FINE = 4
# «Обрывает» всерьёз: не меньше стольких серверов и не меньше трети проверенных.
WIDESPREAD_CUTS = 3
# Почему сервер проверить не удалось — без догадок о причине.
_UNREACHED = {
    "connect": "нет соединения с сервером",
    "timeout": "сервер не ответил",
    "reset": "соединение сброшено",
    "tls": "шифрование не установилось",
    "cert": "у сервера другой сертификат (он мог переехать)",
    "cancelled": "проверку прервали",
}
# Сколько адресов одного провайдера пробовать, если первый не дал ответа.
CANDIDATES_PER_PROVIDER = 3
# Запасной адрес берётся, только пока с начала проверки прошло меньше этого:
# иначе запасные попытки не уложатся в общий лимит BlockCheck и оборвут
# проверку сайтов.
FALLBACK_BUDGET = 14.0
# Провайдеры в порядке предпочтения: крупные хостинги, у которых ТСПУ
# обрывает загрузку чаще всего.
_PREFERRED_PROVIDERS = ("Akamai", "AWS", "Cloudflare", "CDN77", "Hetzner", "OVH", "DigitalOcean", "Fastly")


class FreezeState(Enum):
    OK = "ok"
    FREEZE = "freeze"
    UNKNOWN = "unknown"


DIRECTION_DOWNLOAD = "download"
DIRECTION_UPLOAD = "upload"


@dataclass(frozen=True, slots=True)
class FreezeServer:
    name: str
    state: FreezeState
    text: str
    # В какую сторону шли данные, когда соединение замерло.
    direction: str = DIRECTION_DOWNLOAD
    provider: str = ""
    host: str = ""
    # Метка сервера в списке: страна и номер («DE.AWS-01»).
    ident: str = ""
    seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FreezeReport:
    level: Level
    headline: str
    servers: tuple[FreezeServer, ...]
    advice: tuple[str, ...] = ()


def pick_freeze_targets(
    targets,
    count: int = TARGETS_COUNT,
    per_provider: int = CANDIDATES_PER_PROVIDER,
) -> list[tuple[str, list[dict]]]:
    """Провайдеры для проверки и до ``per_provider`` HTTPS-адресов у каждого.

    Первый адрес — основной, остальные — запасные на случай, если он не ответит.
    Порядок постоянный: сначала крупные хостинги из ``_PREFERRED_PROVIDERS``.
    """
    by_provider: dict[str, list[dict]] = {}
    for item in targets:
        url = str(item.get("url") or "")
        provider = str(item.get("provider") or "")
        if not url.startswith("https://"):
            continue
        candidates = by_provider.setdefault(provider, [])
        if len(candidates) < max(1, int(per_provider)):
            candidates.append(item)
    names = [name for name in _PREFERRED_PROVIDERS if name in by_provider]
    names += [name for name in by_provider if name not in _PREFERRED_PROVIDERS]
    return [(name, by_provider[name]) for name in names[: max(0, int(count))]]


def every_freeze_target(targets) -> list[tuple[str, list[dict]]]:
    """Все HTTPS-адреса списка, каждый отдельной проверкой: так идёт полная проверка."""
    return [
        (str(item.get("provider") or ""), [item])
        for item in targets
        if str(item.get("url") or "").startswith("https://")
    ]


def classify_download(result: ProbeResult | None) -> tuple[FreezeState, str]:
    """Что значит результат загрузки для проверки обрыва."""
    if result is None:
        return FreezeState.UNKNOWN, "не удалось узнать адрес сервера"
    if not result.ok:
        # Причины здесь не называются: мёртвый или переехавший сервер из списка
        # отвечал бы «чужой сертификат» — и это выглядело бы как перехват трафика.
        return FreezeState.UNKNOWN, f"не удалось проверить: {_UNREACHED.get(result.kind, 'сервер не ответил как ожидалось')}"
    size = int(result.body_size)
    kb = size // 1024
    if result.body_cut and FREEZE_MIN_BYTES <= size <= FREEZE_MAX_BYTES:
        return FreezeState.FREEZE, f"загрузка оборвалась на {kb} КБ"
    if size >= FREEZE_MAX_BYTES:
        return FreezeState.OK, f"получено {kb} КБ без обрыва"
    if result.body_cut:
        return FreezeState.UNKNOWN, f"загрузка оборвалась на {kb} КБ — вне окна 14–24 КБ, на этот обрыв не похоже"
    status = int(result.status or 0)
    if status >= 300:
        return FreezeState.UNKNOWN, f"сервер не отдал файл (код {status})"
    return FreezeState.UNKNOWN, f"файл слишком маленький ({kb} КБ) — не показательно"


def _split_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.hostname or "", path


def _confirm_cut(first_text: str, again: tuple[FreezeState, str]) -> tuple[FreezeState, str]:
    """Обрыв считается обрывом, только если повторился: фильтр режет каждый раз, сбой сети — нет."""
    state, text = again
    if state == FreezeState.FREEZE:
        return FreezeState.FREEZE, f"{text} — дважды подряд"
    if state == FreezeState.OK:
        return FreezeState.OK, f"{text} со второго раза: первый обрыв был случайным сбоем"
    return FreezeState.UNKNOWN, f"{first_text}, но при повторе обрыв не подтвердился ({text})"


def _check_provider(
    provider: str,
    candidates: list[dict],
    download: Callable[[str, str], ProbeResult | None],
    fallback_allowed: Callable[[], bool],
    upload: Callable[[str, str], UploadVerdict | None] | None = None,
) -> FreezeServer:
    """Пробует адреса провайдера по очереди, пока один не даст ясный ответ.

    Если загрузка прошла без обрыва, на том же сервере проверяется отправка:
    ограничение работает в обе стороны.
    """
    started = time.monotonic()
    host = ""
    ident = ""
    state, text = FreezeState.UNKNOWN, "нет адресов для проверки"
    tried = 0
    for item in candidates:
        if tried and not fallback_allowed():
            break
        host, path = _split_url(str(item.get("url") or ""))
        ident = str(item.get("id") or "")
        tried += 1
        state, text = classify_download(download(host, path))
        if state == FreezeState.FREEZE:
            state, text = _confirm_cut(text, classify_download(download(host, path)))
        if state != FreezeState.UNKNOWN:
            break
    if state == FreezeState.UNKNOWN and tried > 1:
        text = f"{text} (запасные адреса тоже не помогли, всего адресов: {tried})"
    name = f"{provider} ({host})" if host else provider
    direction = DIRECTION_DOWNLOAD
    if state == FreezeState.OK and upload is not None:
        verdict = upload(host, path)
        if verdict is not None and verdict.code in (UPLOAD_BULK_STALLS, UPLOAD_PACKET_LIMIT):
            state, text, direction = FreezeState.FREEZE, f"загрузка проходит, но {verdict.text}", DIRECTION_UPLOAD
        elif verdict is not None and verdict.code == UPLOAD_OK:
            text = f"{text}; отправка тоже проходит"
    return FreezeServer(
        name=name,
        state=state,
        text=text,
        direction=direction,
        provider=provider,
        host=host,
        ident=ident,
        seconds=time.monotonic() - started,
    )


def check_freeze(
    submit: Callable[..., Future],
    wait: Callable[[Future], object],
    download: Callable[[str, str], ProbeResult | None],
    upload: Callable[[str, str], UploadVerdict | None] | None = None,
    *,
    budget: float = FALLBACK_BUDGET,
    every: bool = False,
    on_server: Callable[[FreezeServer, int, int], None] | None = None,
) -> tuple[FreezeServer, ...]:
    """Провайдеры проверяются параллельно, адреса одного провайдера — по очереди.

    ``every`` — полная проверка: каждый адрес списка проверяется сам по себе,
    без запасных, не больше ``EVERY_AT_ONCE`` одновременно (десятки соединений
    разом сами дали бы ложные обрывы). ``on_server(сервер, готово, всего)``
    зовётся по мере готовности — для хода проверки на экране.

    ``download(host, path)`` и ``upload(host, path)`` сами выбирают IP. Запасной
    адрес пробуется, только пока не вышел ``budget`` секунд с начала проверки.
    Отправка проверяется, только если загрузка с этого сервера прошла.
    """
    from blockcheck.data_lists import TCP_16_20_TARGETS

    started = time.monotonic()

    def _fallback_allowed() -> bool:
        return time.monotonic() - started < budget

    picked = every_freeze_target(TCP_16_20_TARGETS) if every else pick_freeze_targets(TCP_16_20_TARGETS)
    gate = threading.BoundedSemaphore(EVERY_AT_ONCE)
    lock = threading.Lock()
    done = [0]

    def _one(provider: str, candidates: list[dict]) -> FreezeServer:
        with gate:
            server = _check_provider(provider, candidates, download, _fallback_allowed, upload)
        if on_server is not None:
            with lock:
                done[0] += 1
                ready = done[0]
            try:
                on_server(server, ready, len(picked))
            except Exception:
                pass
        return server

    planned = [(provider, submit(_one, provider, candidates)) for provider, candidates in picked]
    servers: list[FreezeServer] = []
    for provider, future in planned:
        try:
            servers.append(wait(future))
        except Exception as exc:
            servers.append(
                FreezeServer(name=provider, state=FreezeState.UNKNOWN, text=f"ошибка проверки ({exc})", provider=provider)
            )
    return tuple(servers)


def summarize_freeze(servers: tuple[FreezeServer, ...], *, zapret_running: bool | None) -> FreezeReport:
    frozen = [item for item in servers if item.state == FreezeState.FREEZE]
    fine = [item for item in servers if item.state == FreezeState.OK]
    if frozen:
        advice = (
            ("Подберите стратегию: в «Подборе стратегии» укажите сайт, который грузится не до конца.",)
            if zapret_running is not False
            else ("Запустите Zapret на странице «Управление Zapret 2» — стратегии обходят этот обрыв.",)
        )
        directions = {item.direction for item in frozen}
        if directions == {DIRECTION_UPLOAD}:
            what = "отправка данных на зарубежные серверы"
        elif directions == {DIRECTION_DOWNLOAD}:
            what = "загрузка с зарубежных серверов на 16–20 КБ"
        else:
            what = "загрузка с зарубежных серверов на 16–20 КБ и отправка данных на них"
        decided = len(frozen) + len(fine)
        if len(frozen) == 1 and len(fine) >= SINGLE_CUT_NEEDS_FINE:
            # Один сервер из многих — не картина сети: так бывает из-за самого сервера.
            return FreezeReport(
                Level.OK,
                f"Обрыва на 16–20 КБ нет: без обрыва {len(fine)} из {decided} серверов, один оборвался — "
                "единичный случай",
                servers,
            )
        # «Обрывает» — когда это видно у нескольких серверов и у заметной их доли.
        widespread = len(frozen) >= WIDESPREAD_CUTS and len(frozen) * 3 >= decided
        return FreezeReport(
            Level.FAIL if widespread else Level.WARN,
            f"Обрывается {what}: {len(frozen)} из {decided} проверенных серверов",
            servers,
            advice,
        )
    unknown = len(servers) - len(fine)
    if fine and not unknown:
        return FreezeReport(Level.OK, "Обрыва загрузки на 16–20 КБ нет", servers)
    if fine and len(fine) >= unknown:
        return FreezeReport(
            Level.OK,
            f"Обрыва на 16–20 КБ нет: проверено {len(fine)} из {len(servers)} серверов, "
            "остальные проверить не удалось",
            servers,
        )
    if fine:
        return FreezeReport(
            Level.UNKNOWN,
            f"Обрыв на 16–20 КБ проверен не до конца: без обрыва {len(fine)} из {len(servers)} серверов, "
            "остальные не ответили",
            servers,
        )
    return FreezeReport(
        Level.UNKNOWN,
        "Обрыв на 16–20 КБ проверить не удалось: тестовые серверы не ответили",
        servers,
    )
