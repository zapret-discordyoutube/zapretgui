"""Где стоит фильтр провайдера (ТСПУ): поиск по нескольким сайтам и общий вывод.

Это «умная трассировка». Обычная показывает узлы по дороге до сервера; эта
ещё находит, после какого узла соединение с заблокированным сайтом начинают
резать. Как именно ищется место на одном сайте — в ``diagnostics.path_trace``:
пакет с запрещённым именем отправляется с таким сроком жизни (TTL), чтобы он
погиб на ``k``-м узле, и смотрим, успел ли фильтр его увидеть.

Зачем несколько сайтов. Один сайт — одна дорога и одна случайность. Если по
трём сайтам с разными адресами фильтр оказался на одном и том же узле — это
место фильтра. Если на разных — так и говорим, без одной общей цифры.

Что получается на выходе:

- **место** — между какими узлами стоит фильтр и чья это сеть (ваш роутер,
  ваш провайдер, стык с другим оператором, вышестоящий оператор);
- **уверенность** — на скольких сайтах и какими способами совпало;
- **срок жизни для поддельных пакетов** — стратегии обхода шлют «обманку»,
  которая должна дойти до фильтра, но не до сервера. Зная узел фильтра ``F`` и
  расстояние до сервера ``D``, получаем рабочий диапазон: от ``F`` до ``D−1``.

Когда вывода нет: расстояние до сервера измерить не удалось; поток гаснет
только у самого сервера; контроль с безобидным именем не прошёл; на компьютере
работает своя программа обхода, а «фильтр» нашёлся на первом узле.

Здесь нет сети: пробы, трассировка и поиск владельцев передаются снаружи.
Сбор фактов (``collect``) отделён от выводов (``judge_site``, ``aggregate``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from diagnostics import path_trace
from diagnostics.path_trace import FILTER_AT_TARGET, FILTER_FOUND, FilterFacts, RouteTrace
from utils.address_kinds import AddressKind, address_kind

__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "METHOD_QUIC",
    "METHOD_TCP",
    "STATE_DISAGREE",
    "STATE_DISTURBED",
    "STATE_FOUND",
    "STATE_NONE",
    "STATE_NOT_FOUND",
    "Candidate",
    "HopInfo",
    "Placement",
    "SiteFacts",
    "SiteVerdict",
    "aggregate",
    "collect",
    "describe_hops",
    "judge_site",
    "lines",
    "pick_candidates",
    "report",
]

METHOD_QUIC = "quic"
METHOD_TCP = "tcp"
_METHOD_TITLES = {METHOD_QUIC: "QUIC (UDP 443)", METHOD_TCP: "TCP (сброс соединения)"}

MAX_SITES = 4

# Итог по всем сайтам.
STATE_FOUND = "found"
# Сайты дали разные узлы.
STATE_DISAGREE = "disagree"
# Искали, но на дороге фильтра не видно или способ не сработал.
STATE_NOT_FOUND = "not_found"
# Искать было не по чему: блокировок по имени в этой проверке нет.
STATE_NONE = "none"
# «Фильтр» на первом узле при работающей программе обхода: это она, а не провайдер.
STATE_DISTURBED = "disturbed"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
_CONFIDENCE_TEXT = {CONFIDENCE_HIGH: "высокая", CONFIDENCE_MEDIUM: "средняя", CONFIDENCE_LOW: "низкая"}

# Чей узел.
OWNER_ROUTER = "router"
OWNER_PROVIDER = "provider"
OWNER_OTHER = "other"
OWNER_SILENT = "silent"
OWNER_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Сайт, по которому можно искать фильтр, и способы, которые к нему применимы."""

    host: str
    ip: str
    methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SiteFacts:
    host: str
    ip: str
    trace: RouteTrace | None = None
    # (способ, что нашёл) — по одному на применимый способ.
    found: tuple[tuple[str, FilterFacts], ...] = ()


@dataclass(frozen=True, slots=True)
class MethodVerdict:
    method: str
    code: str
    hop: int | None
    text: str


@dataclass(frozen=True, slots=True)
class SiteVerdict:
    host: str
    ip: str
    # ``FILTER_FOUND`` — место названо; иначе код причины из ``path_trace``.
    code: str
    hop: int | None
    # Сколько узлов до сервера (наименьшее из измеренного). 0 — неизвестно.
    distance: int
    text: str
    methods: tuple[MethodVerdict, ...] = ()
    # Сколько способов назвали этот узел.
    agreeing: int = 0


@dataclass(frozen=True, slots=True)
class HopInfo:
    ttl: int
    address: str = ""
    rtt_ms: float | None = None
    owner_kind: str = OWNER_UNKNOWN
    # Как назвать владельца человеку: «ваш роутер», «ваш провайдер», «Rostelecom (AS12389)».
    owner: str = ""
    asn: str = ""


@dataclass(frozen=True, slots=True)
class Placement:
    state: str
    sentence: str
    hop: int | None = None
    confidence: str = ""
    # Наименьшее расстояние до сервера среди сайтов, где фильтр найден.
    distance: int = 0
    # Что из этого следует для стратегий обхода. Пусто — сказать нечего.
    ttl_advice: str = ""
    # На чём держится вывод и что его ослабляет.
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _network_of(ip: str) -> str:
    return ".".join(ip.split(".")[:2])


def pick_candidates(sites: Iterable[tuple[str, str, bool, bool]], limit: int = MAX_SITES) -> list[Candidate]:
    """Сайты для поиска: ``(имя, адрес, QUIC режут по имени, TCP рвут сбросом по имени)``.

    Берутся только сайты, где блокировка по имени уже доказана сравнением.
    Сначала по одному из каждой сети: разные адреса — разные дороги и независимые ответы.
    """
    usable: list[Candidate] = []
    for host, ip, quic_blocked, tcp_reset in sites:
        if not ip or ":" in ip or address_kind(ip) != AddressKind.PUBLIC:
            continue
        methods = tuple(
            method for method, on in ((METHOD_QUIC, quic_blocked), (METHOD_TCP, tcp_reset)) if on
        )
        if methods and all(item.ip != ip for item in usable):
            usable.append(Candidate(host, ip, methods))
    # Больше способов — надёжнее сайт; при равенстве порядок отчёта.
    usable.sort(key=lambda item: -len(item.methods))
    picked: list[Candidate] = []
    networks: set[str] = set()
    for item in usable:
        if _network_of(item.ip) not in networks:
            picked.append(item)
            networks.add(_network_of(item.ip))
    picked += [item for item in usable if item not in picked]
    return picked[: max(0, int(limit))]


def collect(
    candidates: Iterable[Candidate],
    *,
    locate: Callable[[str, str, str, RouteTrace | None], FilterFacts],
    trace: Callable[[str], RouteTrace | None],
    submit: Callable,
) -> tuple[SiteFacts, ...]:
    """Факты по каждому сайту. Сайты идут одновременно, способы на одном сайте — по очереди.

    ``trace(адрес)`` — узлы по дороге, ``locate(способ, имя, адрес, дорога)``
    ищет место фильтра. Сначала дорога: по ней видно, сколько узлов до сервера,
    и поиску не приходится перебирать все сроки жизни. Способы по очереди —
    потому что фильтр запоминает соединение, и две пробы к одному адресу разом
    мешали бы друг другу.
    """

    def one(candidate: Candidate) -> SiteFacts:
        try:
            traced = trace(candidate.ip)
        except Exception:
            traced = None
        found = tuple(
            (method, locate(method, candidate.host, candidate.ip, traced)) for method in candidate.methods
        )
        return SiteFacts(candidate.host, candidate.ip, traced, found)

    futures = [submit(one, candidate) for candidate in candidates]
    return tuple(future.result() for future in futures)


def judge_site(facts: SiteFacts) -> SiteVerdict | None:
    """Вывод по одному сайту. None — проверку сняли."""
    methods: list[MethodVerdict] = []
    distances: list[int] = []
    trace = facts.trace if facts.trace is not None and facts.trace.supported else None
    if trace is not None and trace.reached:
        distances.append(len(trace.hops))
    for method, found in facts.found:
        verdict = path_trace.judge_filter(found, trace)
        if verdict is None:
            continue
        if found.distance:
            distances.append(int(found.distance))
        methods.append(MethodVerdict(method, verdict.code, verdict.hop, verdict.text))
    if not methods:
        return None
    distance = min(distances) if distances else 0
    named = [item for item in methods if item.code == FILTER_FOUND and item.hop]
    hops = {item.hop for item in named}
    if len(hops) == 1:
        best = named[0]
        return SiteVerdict(facts.host, facts.ip, FILTER_FOUND, best.hop, distance, best.text, tuple(methods), len(named))
    if len(hops) > 1:
        both = " и ".join(f"{_METHOD_TITLES[item.method]} — узел {item.hop}" for item in named)
        return SiteVerdict(
            facts.host,
            facts.ip,
            path_trace.FILTER_UNSURE,
            None,
            distance,
            f"два способа назвали разные узлы ({both}) — место не подтверждено",
            tuple(methods),
        )
    # Ни один способ места не назвал: показываем самый содержательный отказ.
    order = (FILTER_AT_TARGET, path_trace.FILTER_UNSURE, path_trace.FILTER_NOT_ON_PATH, path_trace.FILTER_NOT_STATEFUL)
    best = min(methods, key=lambda item: order.index(item.code) if item.code in order else len(order))
    return SiteVerdict(facts.host, facts.ip, best.code, None, distance, best.text, tuple(methods))


def describe_hops(
    trace: RouteTrace | None,
    *,
    own_asn: str = "",
    owner_of: Callable[[str], tuple[str, str] | None] = lambda _ip: None,
) -> tuple[HopInfo, ...]:
    """Узлы дороги с владельцами. ``owner_of(адрес)`` → (номер сети AS, название) или None."""
    if trace is None or not trace.supported:
        return ()
    hops: list[HopInfo] = []
    for hop in trace.hops:
        if not hop.address:
            hops.append(HopInfo(hop.ttl, owner_kind=OWNER_SILENT, owner="не отвечает на пинг"))
            continue
        kind = address_kind(hop.address)
        if kind == AddressKind.LOCAL:
            first = hop.ttl == 1
            hops.append(
                HopInfo(
                    hop.ttl,
                    hop.address,
                    hop.rtt_ms,
                    OWNER_ROUTER if first else OWNER_PROVIDER,
                    "ваш роутер" if first else "внутренняя сеть провайдера",
                )
            )
            continue
        if kind == AddressKind.CARRIER:
            hops.append(HopInfo(hop.ttl, hop.address, hop.rtt_ms, OWNER_PROVIDER, "сеть провайдера (общий адрес, CGNAT)"))
            continue
        found = owner_of(hop.address) if kind == AddressKind.PUBLIC else None
        if not found:
            hops.append(HopInfo(hop.ttl, hop.address, hop.rtt_ms))
            continue
        asn, name = found
        if own_asn and asn == own_asn:
            hops.append(HopInfo(hop.ttl, hop.address, hop.rtt_ms, OWNER_PROVIDER, f"ваш провайдер ({name or 'AS' + asn})", asn))
        else:
            label = f"{name} (AS{asn})" if name and asn else name or (f"AS{asn}" if asn else "")
            hops.append(HopInfo(hop.ttl, hop.address, hop.rtt_ms, OWNER_OTHER if label else OWNER_UNKNOWN, label, asn))
    return tuple(hops)


def _whose(hop: int, hops: tuple[HopInfo, ...]) -> str:
    """Чья сеть на участке перед узлом ``hop``. Пусто — сказать нечего."""
    if hop == 1:
        return "в вашем роутере или на самом компьютере"
    by_ttl = {item.ttl: item for item in hops}
    before, after = by_ttl.get(hop - 1), by_ttl.get(hop)
    kinds = (before.owner_kind if before else OWNER_UNKNOWN, after.owner_kind if after else OWNER_UNKNOWN)
    home = (OWNER_ROUTER, OWNER_PROVIDER)
    if kinds[0] in home and kinds[1] in home:
        return "в сети вашего провайдера"
    if kinds[0] in home and kinds[1] == OWNER_OTHER:
        return f"на выходе из сети вашего провайдера, на стыке с {after.owner}"
    if kinds[0] == OWNER_OTHER and kinds[1] == OWNER_OTHER:
        if before.asn and before.asn == after.asn:
            return f"у вышестоящего оператора {after.owner}"
        return f"у вышестоящих операторов, на стыке {before.owner} и {after.owner}"
    if kinds[0] in home:
        # Следующий узел молчит или неизвестен: фильтр сразу за сетью провайдера.
        return "в сети вашего провайдера или сразу за ней"
    return ""


def _ttl_advice(hop: int, distance: int) -> str:
    if not distance or distance <= hop:
        return (
            f"Поддельным пакетам стратегии нужен срок жизни (TTL) не меньше {hop}: с меньшим они "
            "погибнут раньше фильтра, и он их не увидит."
        )
    if distance - 1 == hop:
        return (
            f"Поддельным пакетам стратегии подходит срок жизни (TTL) ровно {hop}: с меньшим они не дойдут до "
            f"фильтра, а с {distance} и больше дойдут до сервера и сломают соединение."
        )
    return (
        f"Поддельным пакетам стратегии подходит срок жизни (TTL) от {hop} до {distance - 1}: с меньшим они не "
        f"дойдут до фильтра, а с {distance} и больше дойдут до сервера и сломают соединение."
    )


def _sites_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} сайту"
    return f"{count} сайтам"


def aggregate(
    verdicts: Iterable[SiteVerdict],
    hops: tuple[HopInfo, ...] = (),
    *,
    zapret_running: bool | None = None,
    other_tools: Iterable[str] = (),
) -> Placement:
    """Общий вывод по всем сайтам. ``hops`` — узлы дороги того сайта, по которому называем место."""
    verdicts = tuple(verdicts)
    tools = tuple(other_tools)
    local = (["Zapret"] if zapret_running else []) + list(tools)
    if not verdicts:
        why = (
            " Возможно, потому что сейчас работает Zapret: место фильтра ищется на сети без обхода."
            if zapret_running
            else ""
        )
        return Placement(
            STATE_NONE,
            "Блокировок по имени сайта в этой проверке не видно — искать место фильтра не по чему." + why,
        )
    found = [item for item in verdicts if item.code == FILTER_FOUND and item.hop]
    if not found:
        best = verdicts[0]
        if local:
            # Программа обхода переделывает пакеты с запрещённым именем: фильтр на них не
            # срабатывает, и поиск видит «фильтра нет» там, где он есть.
            return Placement(
                STATE_DISTURBED,
                f"Место фильтра не найдено, но во время поиска работал {', '.join(local)}: программа обхода "
                "переделывает пакеты с запрещённым именем, и фильтр на них не срабатывает. Остановите её и "
                "повторите проверку — место фильтра ищется только на сети без обхода.",
                reasons=tuple(f"{item.host}: {item.text}" for item in verdicts),
            )
        return Placement(
            STATE_NOT_FOUND,
            f"Место фильтра определить не удалось: {best.text}.",
            reasons=tuple(f"{item.host}: {item.text}" for item in verdicts),
        )
    counts = Counter(item.hop for item in found)
    hop, votes = counts.most_common(1)[0]
    reasons = [
        f"{item.host}: узел {item.hop}"
        + (f", сервер на узле {item.distance}" if item.distance else "")
        + (" (двумя способами)" if item.agreeing > 1 else "")
        for item in found
    ]
    reasons += [f"{item.host}: {item.text}" for item in verdicts if item not in found]
    if hop == 1 and local:
        return Placement(
            STATE_DISTURBED,
            f"Соединение гасится уже на первом узле, но на компьютере работает {', '.join(local)} — так выглядит "
            "и своя программа обхода. Остановите её и повторите проверку: тогда будет видно место фильтра провайдера.",
            reasons=tuple(reasons),
        )
    if len(counts) > 1:
        spread = ", ".join(f"узел {value}" for value in sorted(counts))
        return Placement(
            STATE_DISAGREE,
            f"По разным сайтам фильтр оказался на разных узлах ({spread}). Так бывает, когда фильтров несколько "
            "или дороги к сайтам расходятся раньше фильтра — одного места назвать нельзя.",
            reasons=tuple(reasons),
        )
    agreeing = max((item.agreeing for item in found), default=1)
    if votes >= 3 or (votes >= 2 and agreeing > 1):
        confidence = CONFIDENCE_HIGH
    elif votes >= 2 or agreeing > 1:
        confidence = CONFIDENCE_MEDIUM
    else:
        confidence = CONFIDENCE_LOW
    distance = min((item.distance for item in found if item.distance), default=0)
    whose = _whose(hop, hops)
    where = "не дальше первого узла от вас" if hop == 1 else f"между узлами {hop - 1} и {hop} от вас"
    basis = f"совпало по {_sites_word(votes)}" if votes > 1 else "найдено по одному сайту"
    if local:
        reasons.append(f"во время поиска работали: {', '.join(local)} — они могли исказить результат")
        # Программа обхода на этом компьютере гасила бы поток уже на первом узле; раз место дальше,
        # это не она. Уверенность всё же на ступень ниже: VPN мог изменить саму дорогу.
        confidence = CONFIDENCE_MEDIUM if confidence == CONFIDENCE_HIGH else CONFIDENCE_LOW
    return Placement(
        STATE_FOUND,
        f"Фильтр стоит {where}" + (f" — {whose}" if whose else "") + f". Уверенность {_CONFIDENCE_TEXT[confidence]}: {basis}.",
        hop=hop,
        confidence=confidence,
        distance=distance,
        ttl_advice=_ttl_advice(hop, distance),
        reasons=tuple(reasons),
    )


def report(
    placement: Placement,
    verdicts: Iterable[SiteVerdict],
    hops: tuple[HopInfo, ...],
    shown: SiteVerdict | None,
) -> dict:
    """Итог для экрана. ``shown`` — сайт, по дороге которого показаны узлы."""
    found = placement.state == STATE_FOUND
    return {
        "state": placement.state,
        "found": found,
        "hop": placement.hop,
        "confidence": placement.confidence,
        "distance": placement.distance,
        "text": placement.sentence,
        "ttl_advice": placement.ttl_advice,
        "reasons": list(placement.reasons),
        "host": shown.host if shown else "",
        "address": shown.ip if shown else "",
        "sites": [
            {
                "host": item.host,
                "address": item.ip,
                "found": item.code == FILTER_FOUND,
                "code": item.code,
                "hop": item.hop,
                "distance": item.distance,
                "text": item.text,
                "methods": [
                    {"method": part.method, "title": _METHOD_TITLES[part.method], "code": part.code, "hop": part.hop, "text": part.text}
                    for part in item.methods
                ],
            }
            for item in verdicts
        ],
        "hops": [
            {
                "ttl": hop.ttl,
                "address": hop.address,
                "rtt_ms": hop.rtt_ms,
                "owner": hop.owner,
                "owner_kind": hop.owner_kind,
                "asn": hop.asn,
            }
            for hop in hops
        ],
    }


def lines(place: dict) -> list[str]:
    """Раздел «Где стоит фильтр» для текстового отчёта."""
    out = ["", "━━━━━━━━ Где стоит фильтр ━━━━━━━━"]
    icon = {STATE_FOUND: "📍", STATE_DISAGREE: "⚠️", STATE_DISTURBED: "⚠️"}.get(place.get("state"), "ℹ️")
    out.append(f"{icon} {place.get('text', '')}")
    if place.get("ttl_advice"):
        out.append(f"   👉 {place['ttl_advice']}")
    for site in place.get("sites") or ():
        mark = "📍" if site.get("found") else "❔"
        distance = f", до сервера {site['distance']} узлов" if site.get("distance") else ""
        out.append(f"   {mark} {site.get('host', '')} ({site.get('address', '')}{distance}): {site.get('text', '')}")
        for method in site.get("methods") or ():
            out.append(f"        {method.get('title', '')}: {method.get('text', '')}")
    hops = place.get("hops") or ()
    if hops:
        out.append(f"   Дорога до {place.get('host', '')}:")
        for hop in hops:
            if place.get("found") and hop.get("ttl") == place.get("hop"):
                out.append("      ── здесь стоит фильтр ──")
            rtt = hop.get("rtt_ms")
            time_text = "" if not isinstance(rtt, (int, float)) else ("< 1 мс" if rtt < 1 else f"{round(rtt)} мс")
            parts = [part for part in (hop.get("address") or "не ответил", time_text, hop.get("owner") if hop.get("address") else "") if part]
            out.append(f"      {hop.get('ttl', ''):>2}. {' · '.join(parts)}")
    return out
