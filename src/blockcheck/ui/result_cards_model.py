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

from dataclasses import dataclass, replace

from diagnostics.block_view import COMPARE_STATES, FILTER_MARK, BlockView, build_blocks, one_preset_note, registry_rows
from diagnostics.compare import GROUP_TITLES
from diagnostics.levels import level_rank
from diagnostics.service_view import service_sections
from diagnostics.service_view import site_kind as view_site_kind
from diagnostics.service_view import site_level as view_site_level

__all__ = [
    "FILTER_MARK",
    "PREVIEW_LINES",
    "Card",
    "Counter",
    "DotGroup",
    "FindingParts",
    "Line",
    "Section",
    "build_cards",
    "build_counters",
    "read_finding_parts",
]

# Состояние строки: от него зависят значок и цвет.
OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"
INFO = "info"

# Сколько строк помещается на карточке; остальное — в подробностях.
PREVIEW_LINES = 4


@dataclass(frozen=True, slots=True)
class FindingParts:
    """Находка про DNS готовыми частями: экрану не нужно резать фразу."""

    title: str
    # Все серверы парами «название сервиса, адрес», без обрезки.
    servers: tuple[tuple[str, str], ...] = ()
    note: str = ""

    def services(self) -> list[tuple[str, list[str]]]:
        """Серверы по сервисам в порядке появления: у каждого все его адреса — так строятся метки."""
        grouped: dict[str, list[str]] = {}
        for name, address in self.servers:
            addresses = grouped.setdefault(name, [])
            if address and address not in addresses:
                addresses.append(address)
        return list(grouped.items())

    def detail(self) -> str:
        """Подробности для подсказки: по строке на сервис со всеми адресами, затем пояснение."""
        lines = [f"{name}: {', '.join(addresses)}" if addresses else name for name, addresses in self.services()]
        return "\n".join(part for part in (*lines, self.note) if part)


def read_finding_parts(raw: object) -> FindingParts | None:
    """Части находки из отчёта. None — их нет: отчёты прошлых проверок сохранены только с фразой."""
    if not isinstance(raw, dict) or not raw.get("title"):
        return None
    servers = tuple((str(pair[0]), str(pair[1])) for pair in raw.get("servers") or () if len(pair) == 2)
    return FindingParts(str(raw["title"]), servers, str(raw.get("note") or ""))


@dataclass(frozen=True, slots=True)
class Line:
    state: str
    name: str
    text: str = ""
    # Только у выводов про DNS-серверы, и только если проверка отдала их частями.
    parts: FindingParts | None = None
    # Своя страница строки-плитки (ответ одного DNS-сервера): открывается по нажатию на плитку.
    page: Card | None = None


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    lines: tuple[Line, ...] = ()
    # Текст как есть, моноширинным: таблица узлов или полный отчёт.
    text: str = ""
    # Строки раздела — однотипный перечень (узлы дороги, ответы серверов, имена сайтов):
    # показывать сеткой плиток, которую рисует один виджет, а не строкой на каждую.
    tiles: bool = False
    # Значок плитки вместо нейтрального кружка у строк без оценки.
    tile_icon: str = ""


@dataclass(frozen=True, slots=True)
class DotGroup:
    """Один провайдер на карточке хостингов: по точке на каждый его сервер."""

    name: str
    # Состояния серверов: ok / fail / unknown.
    states: tuple[str, ...]
    # Подсказка к каждой точке, в том же порядке.
    hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Mark:
    """Одна «дорога» к сайту на его карточке: TLS 1.2, TLS 1.3, как Chrome, HTTP, QUIC, DNS.

    У каждого сайта они стоят в одном порядке и на одних местах — так видно с
    одного взгляда, что именно режется, а не приходится читать россыпь меток.
    """

    label: str
    word: str
    state: str
    icon: str
    # Что это за дорога простыми словами — подсказка при наведении: «TLS», «QUIC» человеку ничего не говорят.
    hint: str = ""


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
    # Дороги к сайту на постоянных местах. Когда они есть, на карточке вместо всех меток
    # показывают их и ``tags`` — метки о том, чего в дорогах нет (реестр РКН, обрыв на 16 КБ).
    marks: tuple[Mark, ...] = ()
    tags: tuple[tuple[str, str], ...] = ()
    dots: tuple[DotGroup, ...] = ()
    sections: tuple[Section, ...] = ()
    # Карточка сайта или отдельной проверки: у них разные ряды в сетке.
    site: bool = False
    # Широкая карточка занимает весь ряд.
    wide: bool = False
    # Сам сайт открывается, а эти его части — нет (из отчёта, ``broken_parts``).
    broken_parts: tuple[str, ...] = ()


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
    "whatsapp": "fa5b.whatsapp",
    "signal": "fa5s.comment-dots",
    "messenger": "fa5b.facebook-messenger",
    "twitch": "fa5b.twitch",
    "soundcloud": "fa5b.soundcloud",
    "dailymotion": "fa5b.dailymotion",
    "patreon": "fa5b.patreon",
    "github": "fa5b.github",
    "docker": "fa5b.docker",
    "chatgpt": "fa5s.robot",
    "deepl": "fa5s.language",
    "canva": "fa5s.palette",
    "coursera": "fa5s.graduation-cap",
    "proton": "fa5s.shield-alt",
    "torproject": "fa5s.user-secret",
    "amnezia": "fa5s.key",
    "meduza": "fa5s.newspaper",
    "dw": "fa5s.newspaper",
    "bbc": "fa5s.newspaper",
    "svoboda": "fa5s.newspaper",
    "moscowtimes": "fa5s.newspaper",
    "nnmclub": "fa5s.magnet",
    "rezka": "fa5s.film",
    "speedtest": "fa5s.tachometer-alt",
    "gosuslugi": "fa5s.landmark",
}


def _state(value: object, default: str = UNKNOWN) -> str:
    text = str(value or "")
    return text if text in (OK, WARN, FAIL, UNKNOWN, INFO) else default


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


# ---------------------------------------------------------------------------
# Сайты
# ---------------------------------------------------------------------------


def site_level(service: dict) -> str:
    """Уровень карточки сайта — как в отчёте: экран его не пересчитывает."""
    return view_site_level(service)


def site_kind(service: dict, level: str) -> str:
    """Вид блокировки сайта — как в отчёте. Пусто — сайт открывается или вид неизвестен."""
    return view_site_kind(service, level)


def _target_state(item: dict) -> str:
    if item.get("ok"):
        return OK
    return UNKNOWN if str(item.get("state") or "") == UNKNOWN else FAIL


def site_card(service: dict) -> Card:
    """Карточка сайта по записи сервиса из отчёта (``report["services"]`` или ``engine.check_site``)."""
    return _site_card(service)


# В ячейке сетки места на одно-два слова.
_MARK_WORDS = {"нет соединения": "нет связи"}


# Значок дороги — по её ключу из отчёта. Это вид: какие бывают дороги и что они значат, решает отчёт.
_ROAD_ICONS = {
    "tls12": "fa5s.lock",
    "tls13": "fa5s.lock",
    "browser": "fa5b.chrome",
    "http": "fa5s.unlock-alt",
    "quic": "fa5s.bolt",
    "transfer": "fa5s.tachometer-alt",
    "dns": "fa5s.exchange-alt",
}


def _shown_state(value) -> str:
    """Состояние дороги или метки из отчёта; незнакомое показывается как «неизвестно»."""
    state = str(value or "")
    return state if state in (OK, WARN, FAIL, INFO, UNKNOWN) else UNKNOWN


def _site_card(service: dict) -> Card:
    key = str(service.get("key") or "")
    targets = list(service.get("targets") or ())
    level = site_level(service)
    kind = site_kind(service, level)
    # Слово итога, дороги и метки — из отчёта как есть: экран их не выводит и не дополняет.
    # У отчёта, сохранённого до появления этих полей, их нет — карточка покажет только то, что в нём есть.
    status, roads, tag_items = str(service.get("status") or ""), service.get("roads") or (), service.get("tags") or ()

    lines = tuple(
        Line(_target_state(item), str(item.get("purpose") or item.get("host") or ""), str(item.get("short") or ""))
        for item in targets
    )
    marks = [
        Mark(
            str(road.get("label") or ""),
            # Сокращение слова под ширину ячейки — вид; само слово пришло из отчёта.
            _MARK_WORDS.get(str(road.get("word") or ""), str(road.get("word") or "")),
            _shown_state(road.get("state")),
            _ROAD_ICONS.get(str(road.get("key") or ""), "fa5s.plug"),
            "\n".join(part for part in (str(road.get("hint") or ""), str(road.get("text") or "")) if part),
        )
        for road in roads
    ]
    all_tags = [(str(item.get("key") or ""), str(item.get("text") or ""), _shown_state(item.get("state"))) for item in tag_items]
    # На самой карточке метка причины не нужна — она повторяла бы слово итога под названием сайта.
    # Это выбор вида; сама причина приходит из отчёта и идёт в полный набор меток ниже.
    tags = [(text, state) for tag_key, text, state in all_tags if tag_key != "cause"]
    # Полный набор меток — для шапки отчёта и подсказки: дороги словами, затем метки отчёта (причина первой).
    chips: list[tuple[str, str]] = [(f"{mark.label}: {mark.word}", mark.state) for mark in marks if mark.word != "—"]
    chips.extend((text, state) for _tag_key, text, state in all_tags)

    # Подробности — из общего модуля слов (``diagnostics.service_view``): тот же набор строк и в тексте отчёта.
    detail = _sections_from_view(service_sections(service))

    return Card(
        key=f"site:{key}",
        icon=_SITE_ICONS.get(key, "fa5s.globe"),
        title=str(service.get("label") or ""),
        level=level,
        status=status,
        kind=kind,
        lines=lines,
        chips=tuple(chips),
        marks=tuple(marks),
        tags=tuple(tags),
        sections=tuple(detail),
        site=True,
        broken_parts=tuple(str(part) for part in service.get("broken_parts") or () if part),
    )


# ---------------------------------------------------------------------------
# Отдельные проверки
# ---------------------------------------------------------------------------

_SERVER_STATE = {"ok": OK, "freeze": FAIL, "fail": FAIL, "unknown": UNKNOWN}

# Значок карточки отдельной проверки: это про экран, поэтому живёт здесь, а не в общем модуле слов.
_BLOCK_ICONS = {
    "network": "fa5s.wifi",
    "hostings": "fa5s.server",
    "voice": "fa5s.phone-alt",
    "telegram": "fa5b.telegram-plane",
    "dns": "fa5s.exchange-alt",
    "dns_servers": "fa5s.network-wired",
    "speed": "fa5s.tachometer-alt",
    "ipv6": "fa5s.project-diagram",
    "filter": "fa5s.route",
    "habits": "fa5s.cut",
    "system": "fa5s.desktop",
    "crowd": "fa5s.layer-group",
    "quic_fallback": "fa5s.undo",
    "compare": "fa5s.balance-scale",
}


def _line_of(row) -> Line:
    """Строка карточки по строке общего модуля слов; находка про DNS-серверы несёт свои части."""
    parts = read_finding_parts(row.extra) if isinstance(row.extra, dict) else None
    return Line(row.level, row.title, row.text, parts=parts)


def _sections_from_view(sections) -> list[Section]:
    """Разделы общего модуля слов — разделами карточки: уровень и слова уже решены там."""
    return [
        Section(item.title, tuple(_line_of(row) for row in item.rows), text=item.text, tiles=item.tiles, tile_icon=item.tile_icon)
        for item in sections
    ]


def _hostings_dots(freeze: dict) -> tuple[DotGroup, ...]:
    """Точки по серверам каждого провайдера: цвет — исход, подсказка — слова сервера."""
    by_provider: dict[str, list[dict]] = {}
    for item in freeze.get("servers") or ():
        by_provider.setdefault(str(item.get("provider") or "Без названия"), []).append(item)
    return tuple(
        DotGroup(
            name,
            tuple(_SERVER_STATE.get(str(item.get("state")), UNKNOWN) for item in items),
            tuple(f"{item.get('id', '')} · {item.get('host', '')}: {item.get('text', '')}".strip(" ·") for item in items),
        )
        for name, items in by_provider.items()
    )


def _block_card(view: BlockView, report: dict) -> Card:
    """Карточка отдельной проверки: слова из ``block_view``, от себя — значок и то, что есть только на экране."""
    extras: dict = {}
    if view.key == "hostings":
        extras = {"dots": _hostings_dots(report["freeze"]), "wide": True}
    sections = tuple(_sections_from_view(view.sections))
    if view.key == "compare":
        sections = tuple(_with_site_pages(section, report) for section in sections)
    return Card(
        key=view.key,
        icon=_BLOCK_ICONS.get(view.key, "fa5s.circle"),
        title=view.title,
        level=view.level,
        status=view.status,
        lines=tuple(_line_of(row) for row in view.preview),
        sections=sections,
        **extras,
    )


def _with_site_pages(section: Section, report: dict) -> Section:
    """Плитка сайта в «С Zapret и без» открывает его страницу: что было в обеих проверках и все пробы этой."""
    if not section.tiles:
        return section
    key = next((group for group, name in ((group, GROUP_TITLES[group]) for group in COMPARE_STATES) if name == section.title), "")
    services = report.get("services") or ()
    return replace(
        section,
        lines=tuple(replace(line, page=_compare_site_page(line.name, key, report["compare"], services)) for line in section.lines),
    )


# ---------------------------------------------------------------------------
# Всё вместе
# ---------------------------------------------------------------------------


def build_cards(report: dict) -> list[Card]:
    """Карточки по отчёту: сначала сайты (сломанные впереди), затем остальные проверки."""
    services = list(report.get("services") or ())
    cards = [_site_card(service) for service in sorted(services, key=lambda item: level_rank(site_level(item)))]
    cards.extend(_block_card(view, report) for view in build_blocks(report))
    run = _run_card(report)
    if run is not None:
        cards.append(run)
    return cards




# Что было с сайтом в двух проверках — по группе сравнения: (с Zapret, без Zapret).
_COMPARE_OPENS = {
    "helped": ("открывается", "не открывается"),
    "not_helped": ("не открывается", "не открывается"),
    "broken": ("не открывается", "открывается"),
    "fine_anyway": ("открывается", "открывается"),
}


def _compare_site_page(name: str, key: str, compare: dict, services) -> Card:
    """Страница сайта из сравнения: что было с Zapret и без, и все пробы этой проверки."""
    from diagnostics.history import format_time

    preset = str(compare.get("preset") or "")
    with_zapret, without = _COMPARE_OPENS[key]
    now, past = ("С Zapret", "Без Zapret") if compare.get("zapret_in") == "current" else ("Без Zapret", "С Zapret")
    now_text, past_text = (with_zapret, without) if compare.get("zapret_in") == "current" else (without, with_zapret)
    state = COMPARE_STATES[key]
    verdict = Section(
        "С Zapret и без",
        (
            Line(state, "Итог", GROUP_TITLES[key]),
            Line(OK if now_text == "открывается" else FAIL, f"{now} (эта проверка)", now_text),
            Line(
                OK if past_text == "открывается" else FAIL,
                f"{past} (проверка {format_time(str(compare.get('other_time') or ''))})",
                past_text,
            ),
            Line(INFO, "Пресет", preset or "не определён"),
            Line(INFO, one_preset_note(preset)),
        ),
    )
    service = next((item for item in services if str(item.get("label") or "") == name), None)
    if service is None:
        # Сайта нет в этой проверке (другой набор сайтов): остаётся само сравнение.
        return Card(f"compare:{name}", "fa5s.globe", name, state, GROUP_TITLES[key], sections=(verdict,), site=True)
    site = _site_card(service)
    # Уровень остаётся от самой проверки сайта: по нему рисуется дорога и место, где режут.
    return replace(site, key=f"compare:{site.key}", status=GROUP_TITLES[key], kind="", sections=(verdict, *site.sections))


def _run_card(report: dict) -> Card | None:
    """«Ход проверки»: сколько шёл каждый шаг и при каких условиях проверяли."""
    from diagnostics.report_text import STEP_TITLES

    seconds = {str(name): float(value) for name, value in (report.get("step_seconds") or {}).items()}
    times = tuple(
        Line(INFO, STEP_TITLES.get(name, name).capitalize(), f"{value:.0f} с") for name, value in seconds.items()
    )
    conditions = [Line(INFO, str(line)) for line in report.get("environment") or () if str(line).strip()]
    if report.get("zapret_line"):
        conditions.append(Line(INFO, str(report["zapret_line"])))
    if report.get("preset"):
        conditions.append(Line(INFO, "Выбранный пресет", str(report["preset"])))
    tools = [str(name) for name in report.get("other_bypass_tools") or ()]
    # В отчётах до появления этого поля его нет: тогда считаем, что мешали все запущенные.
    in_path = report.get("tools_in_path")
    in_path = tools if in_path is None else [str(name) for name in in_path]
    from utils.bypass_tools import tool_kind

    idle = [name for name in tools if name not in in_path]
    proxies = [name for name in idle if tool_kind(name) == "proxy"]
    vpns = [name for name in idle if tool_kind(name) == "vpn"]
    if in_path:
        conditions.append(Line(WARN, "На дороге проверки стояли", ", ".join(in_path)))
    if vpns and not any(name.startswith("VPN-подключение") for name in in_path):
        conditions.append(Line(INFO, "VPN запущен, но не подключён", ", ".join(vpns)))
    if proxies:
        conditions.append(Line(INFO, "Прокси (проверка ходит мимо него)", ", ".join(proxies)))
    tools = in_path
    listing = tuple(_line_of(row) for row in registry_rows(report.get("registry") or {}))
    if not times and not conditions and not listing:
        return None
    sections = []
    if times:
        sections.append(Section("Время по шагам", times))
    if conditions:
        sections.append(Section("Условия проверки", tuple(conditions)))
    if listing:
        sections.append(Section("Список реестра РКН", listing))
    longest = max(seconds.items(), key=lambda item: item[1], default=None)
    status = f"Дольше всего: {STEP_TITLES.get(longest[0], longest[0])}, {longest[1]:.0f} с" if longest else "Условия проверки"
    return Card(
        key="run",
        icon="fa5s.stopwatch",
        title="Ход проверки",
        level=WARN if tools else INFO,
        status=status,
        lines=(times or tuple(conditions) or listing)[:PREVIEW_LINES],
        sections=tuple(sections),
    )


def build_counters(report: dict) -> list[Counter]:
    """«Что проверено»: числа, по которым виден объём работы."""
    services = list(report.get("services") or ())
    targets = sum(len(service.get("targets") or ()) for service in services)
    counters = [Counter(len(services), "сайтов", "fa5s.globe"), Counter(targets, "адресов сайтов", "fa5s.link")]
    protocols = sum(len(item.get("protocols") or ()) for service in services for item in service.get("targets") or ())
    if protocols:
        counters.append(Counter(protocols, "проб TLS 1.2 / 1.3 / Chrome / HTTP", "fa5s.lock"))
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
