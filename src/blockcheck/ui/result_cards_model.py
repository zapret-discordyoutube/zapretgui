"""Что показать на карточках результата BlockCheck — без окон, только данные.

Проверка возвращает большой словарь. Здесь он превращается в список карточек:
по одной на сайт и по одной на каждую отдельную проверку (хостинги, звонки,
IPv6, DNS, место фильтра, компьютер). У карточки две стороны:

- коротко — значок, название, слово результата, несколько строк и метки;
  это видно сразу, в сетке;
- подробно — разделы со всеми измерениями; это открывается на весь экран по
  нажатию на карточку.

Экран (``result_cards``) только рисует готовое, поэтому всё, что решается
про слова и порядок, лежит здесь и проверяется тестом без окна.
"""

from __future__ import annotations

from dataclasses import dataclass

from diagnostics.block_kind import KIND_OTHER, KINDS, kind_info

__all__ = [
    "Card",
    "Counter",
    "DotGroup",
    "Line",
    "Section",
    "build_cards",
    "build_counters",
    "FILTER_MARK",
    "PREVIEW_LINES",
]

# Состояние строки: от него зависят значок и цвет.
OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"
INFO = "info"

FILTER_MARK = "здесь стоит фильтр"
# Сколько строк помещается на карточке; остальное — в подробностях.
PREVIEW_LINES = 4


@dataclass(frozen=True, slots=True)
class Line:
    state: str
    name: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    lines: tuple[Line, ...] = ()
    # Текст как есть, моноширинным: таблица узлов или полный отчёт.
    text: str = ""


@dataclass(frozen=True, slots=True)
class DotGroup:
    """Один провайдер на карточке хостингов: по точке на каждый его сервер."""

    name: str
    # Состояния серверов: ok / fail / unknown.
    states: tuple[str, ...]
    # Подсказка к каждой точке, в том же порядке.
    hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Card:
    key: str
    icon: str
    title: str
    level: str
    # Слово результата: «Открывается», «По имени (SNI)», «Обрыв у 12 из 59».
    status: str
    # Вид блокировки (ip / sni / cut16 …): от него цвет. Пусто — цвет по уровню.
    kind: str = ""
    lines: tuple[Line, ...] = ()
    # Короткие метки под строками: (текст, состояние).
    chips: tuple[tuple[str, str], ...] = ()
    dots: tuple[DotGroup, ...] = ()
    sections: tuple[Section, ...] = ()
    # Карточка сайта или отдельной проверки: у них разные ряды в сетке.
    site: bool = False
    # Широкая карточка занимает весь ряд.
    wide: bool = False


@dataclass(frozen=True, slots=True)
class Counter:
    value: int
    caption: str
    icon: str


_SITE_ICONS = {
    "discord": "fa5b.discord",
    "youtube": "fa5b.youtube",
    "telegram": "fa5b.telegram-plane",
    "instagram": "fa5b.instagram",
    "facebook": "fa5b.facebook",
    "x": "fa5b.twitter",
    "linkedin": "fa5b.linkedin",
    "spotify": "fa5b.spotify",
    "rutracker": "fa5s.magnet",
    "google": "fa5b.google",
    "cloudflare": "fa5b.cloudflare",
    "yandex": "fa5b.yandex",
    "vk": "fa5b.vk",
}
_SITE_STATUS = {OK: "Открывается", WARN: "Есть проблемы", FAIL: "Не открывается", UNKNOWN: "Не удалось проверить"}
_CAUSE_WORDS = {
    "by_name": "блокировка по имени",
    "by_address": "закрыт адрес",
    "stub_page": "страница провайдера",
    "address_closed": "адрес закрыт",
    "address_silent": "адрес молчит",
}
_LEVEL_ORDER = {FAIL: 0, WARN: 1, UNKNOWN: 2, OK: 3}


def _state(value: object, default: str = UNKNOWN) -> str:
    text = str(value or "")
    return text if text in (OK, WARN, FAIL, UNKNOWN, INFO) else default


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


# ---------------------------------------------------------------------------
# Сайты
# ---------------------------------------------------------------------------


def site_level(service: dict) -> str:
    """Уровень карточки сайта. Подмена DNS при открывающемся сайте — не «проблема
    сайта»: о ней общая строка в итоге, а здесь — метка."""
    level = _state(service.get("level"))
    targets = list(service.get("targets") or ())
    if level == WARN and targets and all(item.get("ok") for item in targets):
        return OK
    return level


def site_kind(service: dict, level: str) -> str:
    """Вид блокировки сайта. Пусто — сайт открывается или вид неизвестен."""
    kind = str(service.get("kind") or "")
    return kind if level in (FAIL, WARN) and kind in KINDS and kind != KIND_OTHER else ""


def _target_state(item: dict) -> str:
    if item.get("ok"):
        return OK
    return UNKNOWN if str(item.get("state") or "") == UNKNOWN else FAIL


def _site_card(service: dict) -> Card:
    key = str(service.get("key") or "")
    targets = list(service.get("targets") or ())
    level = site_level(service)
    kind = site_kind(service, level)
    status = _capital(kind_info(kind).short) if kind else _SITE_STATUS[level]

    lines = tuple(
        Line(_target_state(item), str(item.get("purpose") or item.get("host") or ""), str(item.get("short") or ""))
        for item in targets
    )
    chips: list[tuple[str, str]] = []
    for word in dict.fromkeys(_CAUSE_WORDS[item["cause"]] for item in targets if item.get("cause") in _CAUSE_WORDS):
        chips.append((word, FAIL))
    quic = {str(item.get("quic") or "") for item in targets}
    if "blocked_by_name" in quic:
        chips.append(("QUIC закрыт", WARN))
    elif "ok" in quic:
        chips.append(("QUIC работает", OK))
    if any(item.get("volume") == "cut" for item in targets):
        chips.append(("обрыв на 16 КБ", WARN))
    if service.get("dns_note"):
        chips.append(("DNS подменён", WARN))
    if service.get("control"):
        chips.append(("контрольный", INFO))

    detail: list[Section] = []
    for item in targets:
        rows = [Line(_target_state(item), "Соединение", str(item.get("text") or ""))]
        if item.get("cause_text"):
            rows.append(Line(FAIL, "Как блокируют", str(item["cause_text"])))
        if item.get("quic_text"):
            rows.append(
                Line(WARN if item.get("quic") == "blocked_by_name" else INFO, "QUIC (UDP 443)", str(item["quic_text"]))
            )
        if item.get("volume_text"):
            rows.append(Line(WARN if item.get("volume") == "cut" else INFO, "Объём загрузки", str(item["volume_text"])))
        if item.get("dns_reason"):
            rows.append(Line(WARN if item.get("dns_state") == "spoofed" else INFO, "DNS", str(item["dns_reason"])))
        if item.get("note"):
            rows.append(Line(INFO, "Заметка", str(item["note"])))
        purpose = str(item.get("purpose") or "")
        host = str(item.get("host") or "")
        detail.append(Section(f"{host} — {purpose}" if purpose else host, tuple(rows)))
    advice = [str(item) for item in service.get("advice") or ()]
    if advice:
        detail.append(Section("Что делать", tuple(Line(INFO, item) for item in advice)))
    if service.get("dns_note"):
        detail.append(Section("DNS", (Line(WARN, str(service["dns_note"])),)))
    if kind and kind_info(kind).about:
        detail.append(Section(kind_info(kind).title, (Line(INFO, kind_info(kind).about),)))

    return Card(
        key=f"site:{key}",
        icon=_SITE_ICONS.get(key, "fa5s.globe"),
        title=str(service.get("label") or ""),
        level=level,
        status=status,
        kind=kind,
        lines=lines,
        chips=tuple(chips),
        sections=tuple(detail),
        site=True,
    )


# ---------------------------------------------------------------------------
# Отдельные проверки
# ---------------------------------------------------------------------------

_SERVER_STATE = {"ok": OK, "freeze": FAIL, "fail": FAIL, "unknown": UNKNOWN}


def _hostings_card(freeze: dict) -> Card:
    servers = list(freeze.get("servers") or ())
    by_provider: dict[str, list[dict]] = {}
    for item in servers:
        by_provider.setdefault(str(item.get("provider") or "Без названия"), []).append(item)

    def _count(state: str, direction: str = "") -> int:
        return sum(
            1
            for item in servers
            if item.get("state") == state and (not direction or item.get("direction") == direction)
        )

    cut_down, cut_up = _count("freeze", "download"), _count("freeze", "upload")
    fine, unknown = _count("ok"), _count("unknown")
    cut = cut_down + cut_up
    level = _state(freeze.get("level"))
    if cut:
        status = f"Обрыв у {cut} из {len(servers)}"
    elif fine:
        status = f"Обрыва нет: {fine} из {len(servers)}"
    else:
        status = "Не удалось проверить"

    lines = [Line(OK, "Проходит без обрыва", str(fine))]
    if cut_down:
        lines.append(Line(FAIL, "Обрыв загрузки на 16–20 КБ", str(cut_down)))
    if cut_up:
        lines.append(Line(FAIL, "Обрыв отправки", str(cut_up)))
    if unknown:
        lines.append(Line(UNKNOWN, "Сервер не ответил", str(unknown)))

    dots = tuple(
        DotGroup(
            name,
            tuple(_SERVER_STATE.get(str(item.get("state")), UNKNOWN) for item in items),
            tuple(f"{item.get('id', '')} · {item.get('host', '')}: {item.get('text', '')}".strip(" ·") for item in items),
        )
        for name, items in by_provider.items()
    )
    sections = [
        Section(
            f"{name} — обрыв у {sum(1 for item in items if item.get('state') == 'freeze')} из {len(items)}",
            tuple(
                Line(
                    _SERVER_STATE.get(str(item.get("state")), UNKNOWN),
                    " · ".join(part for part in (str(item.get("id") or ""), str(item.get("host") or "")) if part),
                    _with_seconds(str(item.get("text") or ""), item.get("seconds")),
                )
                for item in items
            ),
        )
        for name, items in by_provider.items()
    ]
    advice = [str(item) for item in freeze.get("advice") or ()]
    if advice:
        sections.insert(0, Section("Что делать", tuple(Line(INFO, item) for item in advice)))
    sections.insert(
        0,
        Section(
            "Что это за проверка",
            (
                Line(
                    INFO,
                    "С каждого сервера качается около 32 КБ. Если загрузка прошла, на тот же сервер "
                    "отправляется 64 КБ одним потоком и 64 байта мелкими пакетами. Обрыв засчитывается, "
                    "только когда короткий запрос тот же сервер принимает.",
                ),
            ),
        ),
    )
    return Card(
        key="hostings",
        icon="fa5s.server",
        title="Зарубежные хостинги",
        level=level,
        status=status,
        lines=tuple(lines),
        dots=dots,
        sections=tuple(sections),
        wide=True,
    )


def _with_seconds(text: str, seconds: object) -> str:
    if isinstance(seconds, (int, float)) and seconds > 0:
        return f"{text} · {seconds:.1f} с"
    return text


_VOICE_STATUS = {OK: "Работают", WARN: "С перебоями", FAIL: "Не работают", UNKNOWN: "Не удалось проверить"}


def _voice_card(voice: dict) -> Card:
    items = list(voice.get("items") or ())
    level = _state(voice.get("level"))
    lines = tuple(
        Line(_SERVER_STATE.get(str(item.get("state")), UNKNOWN), str(item.get("name") or ""), str(item.get("text") or ""))
        for item in items
    )
    answered = sum(1 for item in items if item.get("ok"))
    sections = [Section(str(voice.get("headline") or "Голосовые серверы"), lines)]
    advice = [str(item) for item in voice.get("advice") or ()]
    if advice:
        sections.append(Section("Что делать", tuple(Line(INFO, item) for item in advice)))
    return Card(
        key="voice",
        icon="fa5s.phone-alt",
        title="Голосовые звонки (UDP)",
        level=level,
        status=f"{_VOICE_STATUS[level]}: {answered} из {len(items)}" if items else _VOICE_STATUS[level],
        lines=lines,
        sections=tuple(sections),
    )


# Состояние IPv6 → (уровень, слово). «Нет в сети» — норма, поэтому не зелёное и не красное.
_IPV6 = {
    "ok": (OK, "Работает"),
    "absent": (UNKNOWN, "Нет в этой сети"),
    "broken": (WARN, "Не работает"),
    "unknown": (UNKNOWN, "Не проверено"),
}


def _ipv6_card(ipv6: dict) -> Card:
    level, word = _IPV6.get(str(ipv6.get("state") or ""), _IPV6["unknown"])
    text = f"IPv6 {ipv6.get('text', '')}".strip()
    return Card(
        key="ipv6",
        icon="fa5s.project-diagram",
        title="IPv6",
        level=level,
        status=word,
        lines=(Line(level, text),),
        sections=(Section("IPv6", (Line(level, text),)),),
    )


def _dns_card(report: dict) -> Card | None:
    reference = list(report.get("reference") or ())
    spoofed = [str(item) for item in report.get("spoofed_hosts") or ()]
    if not reference and not spoofed:
        return None
    lines = [
        Line(
            OK if item.get("ok") else FAIL,
            f"{item.get('label', '')} ({item.get('address', '')})",
            "отвечает" if item.get("ok") else str(item.get("reason") or "не отвечает"),
        )
        for item in reference
    ]
    answering = sum(1 for item in reference if item.get("ok"))
    if spoofed:
        level, status = WARN, f"Подмена адресов: {len(spoofed)}"
    elif reference and answering < len(reference):
        level, status = WARN, f"Эталоны: {answering} из {len(reference)}"
    else:
        level, status = OK, "Адреса не подменяются"
    sections = []
    if spoofed:
        sections.append(Section("DNS подменяет адреса", tuple(Line(WARN, host) for host in spoofed)))
        lines.insert(0, Line(WARN, "Подменены адреса", ", ".join(spoofed)))
    sections.append(
        Section(
            "Эталонные серверы: по ним сверяются адреса сайтов",
            tuple(lines[1:] if spoofed else lines),
        )
    )
    return Card(
        key="dns",
        icon="fa5s.exchange-alt",
        title="Подмена DNS",
        level=level,
        status=status,
        lines=tuple(lines),
        sections=tuple(sections),
    )


_DNS_SERVERS_STATUS = {OK: "В порядке", WARN: "Есть замечания", FAIL: "Есть проблемы", UNKNOWN: "Не проверено"}


def _dns_servers_card(dns_servers: dict) -> Card:
    level = _state(dns_servers.get("level"))
    findings = [
        Line(_state(item.get("level"), INFO), str(item.get("text") or ""))
        for item in dns_servers.get("findings") or ()
    ]
    sections = [Section("Выводы", tuple(findings))] if findings else []
    text = str(dns_servers.get("text") or "")
    if text:
        sections.append(Section("Все серверы и способы связи", text=text))
    return Card(
        key="dns_servers",
        icon="fa5s.network-wired",
        title="DNS-серверы",
        level=level,
        status=_DNS_SERVERS_STATUS[level],
        lines=tuple(findings) or (Line(level, "Замечаний нет"),),
        sections=tuple(sections),
    )


def _hop_time(rtt: object) -> str:
    if not isinstance(rtt, (int, float)):
        return ""
    return "< 1 мс" if rtt < 1 else f"{round(rtt)} мс"


def _filter_card(place: dict) -> Card:
    found = bool(place.get("found"))
    text = str(place.get("text") or "")
    hop = place.get("hop") if found else None
    hops = []
    for item in place.get("hops") or ():
        if item.get("ttl") == hop:
            hops.append(Line(FAIL, f"── {FILTER_MARK} ──"))
        hops.append(Line(INFO, f"Узел {item.get('ttl', '')}", " · ".join(
            part for part in (str(item.get("address") or "не ответил"), _hop_time(item.get("rtt_ms"))) if part
        )))
    target = f"{place.get('host', '')} ({place.get('address', '')})"
    sections = [Section(f"По сайту {target}", (Line(WARN if found else INFO, _capital(text)),))]
    if hops:
        sections.append(Section("Узлы по дороге до сервера", tuple(hops)))
    return Card(
        key="filter",
        icon="fa5s.route",
        title="Место фильтра",
        level=WARN if found else UNKNOWN,
        status=f"Перед узлом {hop}" if found and hop else "Не найдено",
        lines=(Line(WARN if found else UNKNOWN, _capital(text)), Line(INFO, "Искали по сайту", str(place.get("host") or ""))),
        sections=tuple(sections),
    )


def _system_card(items: list[dict]) -> Card:
    lines = [
        Line(_state(item.get("level")), str(item.get("title") or ""), str(item.get("text") or "")) for item in items
    ]
    failed = [line for line in lines if line.state == FAIL]
    warned = [line for line in lines if line.state == WARN]
    if failed:
        level, status = FAIL, f"Мешает работе: {len(failed)}"
    elif warned:
        level, status = WARN, f"Замечаний: {len(warned)}"
    elif lines and all(line.state == UNKNOWN for line in lines):
        level, status = UNKNOWN, "Не проверено"
    else:
        level, status = OK, f"В порядке: {sum(1 for line in lines if line.state == OK)} из {len(lines)}"
    shown = sorted(lines, key=lambda line: _LEVEL_ORDER.get(line.state, 4))
    advice = [
        Line(INFO, str(item.get("title") or ""), str(item.get("advice") or ""))
        for item in items
        if item.get("advice") and item.get("level") in (FAIL, WARN)
    ]
    sections = [Section("Что проверено на компьютере", tuple(lines))]
    if advice:
        sections.insert(0, Section("Что делать", tuple(advice)))
    return Card(
        key="system",
        icon="fa5s.desktop",
        title="Этот компьютер",
        level=level,
        status=status,
        lines=tuple(shown),
        sections=tuple(sections),
    )


# ---------------------------------------------------------------------------
# Всё вместе
# ---------------------------------------------------------------------------


def build_cards(report: dict) -> list[Card]:
    """Карточки по отчёту: сначала сайты (сломанные впереди), затем остальные проверки."""
    services = list(report.get("services") or ())
    cards = [_site_card(service) for service in sorted(services, key=lambda item: _LEVEL_ORDER.get(site_level(item), 9))]
    freeze = report.get("freeze")
    if freeze and freeze.get("servers"):
        cards.append(_hostings_card(freeze))
    if report.get("voice"):
        cards.append(_voice_card(report["voice"]))
    dns = _dns_card(report)
    if dns is not None:
        cards.append(dns)
    if report.get("dns_servers"):
        cards.append(_dns_servers_card(report["dns_servers"]))
    if report.get("ipv6"):
        cards.append(_ipv6_card(report["ipv6"]))
    if report.get("filter"):
        cards.append(_filter_card(report["filter"]))
    system = list(report.get("system") or ())
    if system:
        cards.append(_system_card(system))
    return cards


def build_counters(report: dict) -> list[Counter]:
    """«Что проверено»: числа, по которым виден объём работы."""
    services = list(report.get("services") or ())
    targets = sum(len(service.get("targets") or ()) for service in services)
    counters = [Counter(len(services), "сайтов", "fa5s.globe"), Counter(targets, "адресов сайтов", "fa5s.link")]
    quic = sum(1 for service in services for item in service.get("targets") or () if item.get("quic"))
    if quic:
        counters.append(Counter(quic, "проверок QUIC", "fa5s.bolt"))
    servers = len((report.get("freeze") or {}).get("servers") or ())
    if servers:
        counters.append(Counter(servers, "серверов хостингов", "fa5s.server"))
    voice = len((report.get("voice") or {}).get("items") or ())
    if voice:
        counters.append(Counter(voice, "голосовых серверов", "fa5s.phone-alt"))
    reference = len(report.get("reference") or ())
    if reference:
        counters.append(Counter(reference, "эталонных DNS", "fa5s.exchange-alt"))
    system = len(report.get("system") or ())
    if system:
        counters.append(Counter(system, "пунктов о компьютере", "fa5s.desktop"))
    hops = len((report.get("filter") or {}).get("hops") or ())
    if hops:
        counters.append(Counter(hops, "узлов по дороге", "fa5s.route"))
    return [counter for counter in counters if counter.value > 0]
