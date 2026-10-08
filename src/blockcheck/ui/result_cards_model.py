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

from diagnostics.block_kind import KIND_OTHER, KINDS, kind_info

__all__ = [
    "Card",
    "Counter",
    "DotGroup",
    "FindingParts",
    "Line",
    "Section",
    "build_cards",
    "build_counters",
    "read_finding_parts",
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
# Исход попытки основного запроса — словом для подробностей.
_TRIED_WORDS = {
    "ok": "открылся",
    "connect": "нет соединения",
    "timeout": "нет ответа",
    "reset": "сброс",
    "tls": "обрыв на шифровании",
    "cert": "чужой сертификат",
    "error": "ошибка",
}
_PROTOCOL_STATES = {"ok": OK, "fail": FAIL, "info": INFO, "unknown": UNKNOWN}
_SITE_STATUS = {OK: "Открывается", WARN: "Есть проблемы", FAIL: "Не открывается", UNKNOWN: "Не удалось проверить"}
_CAUSE_WORDS = {
    "by_name": "блокировка по имени",
    "name_whitelist": "проходят только разрешённые имена",
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
    if level in (OK, WARN) and targets and all(item.get("ok") for item in targets):
        # Сайт открывается, но приветствие с составом Chrome не проходит: в браузере он может висеть.
        # Или запись в hosts ведёт на нерабочий адрес: проверка сайт открыла, а браузер не откроет.
        return WARN if _by_fingerprint(targets) or any(item.get("hosts_stale") for item in targets) else OK
    return level


def _by_fingerprint(targets: list) -> bool:
    return any(proto.get("code") == "fingerprint" for item in targets for proto in item.get("protocols") or ())


def site_kind(service: dict, level: str) -> str:
    """Вид блокировки сайта. Пусто — сайт открывается или вид неизвестен."""
    kind = str(service.get("kind") or "")
    return kind if level in (FAIL, WARN) and kind in KINDS and kind != KIND_OTHER else ""


def _target_state(item: dict) -> str:
    if item.get("ok"):
        return OK
    return UNKNOWN if str(item.get("state") or "") == UNKNOWN else FAIL


def _registry_text(item: dict) -> str:
    mark = item.get("registry") or {}
    parts = []
    name = str(mark.get("name") or "")
    if name:
        parts.append("сайт значится в реестре" if name == item.get("host") else f"в реестре значится {name}")
    if mark.get("network"):
        parts.append(f"адрес сервера входит в список заблокированных ({mark['network']})")
    if parts:
        return "; ".join(parts)
    # Для открывающегося сайта оговорка лишняя, для закрытого — главное.
    if item.get("ok"):
        return "не значится"
    return "не значится — блокировать могут и без записи в реестре"


def site_card(service: dict) -> Card:
    """Карточка сайта по записи сервиса из отчёта (``report["services"]`` или ``engine.check_site``)."""
    return _site_card(service)


_MARK_ICONS = {"TLS 1.2": "fa5s.lock", "TLS 1.3": "fa5s.lock", "Как Chrome": "fa5b.chrome", "HTTP": "fa5s.unlock-alt"}


# В ячейке сетки места на одно-два слова.
_MARK_WORDS = {"нет соединения": "нет связи"}


def _mark_icon(title: str) -> str:
    return _MARK_ICONS.get(title, "fa5s.plug")


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
    marks: list[Mark] = []
    tags: list[tuple[str, str]] = []
    # Три дороги к главному адресу сайта: TLS 1.2, TLS 1.3 и HTTP.
    main = next((item for item in targets if item.get("main")), targets[0] if targets else {})
    for proto in main.get("protocols") or ():
        title, word = str(proto.get("title") or ""), str(proto.get("word") or "")
        state = _PROTOCOL_STATES.get(str(proto.get("state")), UNKNOWN)
        chips.append((f"{title}: {word}", state))
        marks.append(Mark(title.removeprefix("Как "), _MARK_WORDS.get(word, word), state, _mark_icon(title)))
    for word in dict.fromkeys(_CAUSE_WORDS[item["cause"]] for item in targets if item.get("cause") in _CAUSE_WORDS):
        chips.append((word, FAIL))
    quic = {str(item.get("quic") or "") for item in targets}
    if "blocked_by_name" in quic:
        chips.append(("QUIC закрыт", WARN))
        marks.append(Mark("QUIC", "закрыт", WARN, "fa5s.bolt"))
    elif "ok" in quic:
        chips.append(("QUIC работает", OK))
        marks.append(Mark("QUIC", "работает", OK, "fa5s.bolt"))
    elif marks:
        marks.append(Mark("QUIC", "—", UNKNOWN, "fa5s.bolt"))
    if any(item.get("volume") == "cut" for item in targets):
        tags.append(("обрыв на 16 КБ", WARN))
    dns_states = {str(item.get("dns_state") or "") for item in targets}
    if service.get("dns_note") or "spoofed" in dns_states:
        marks.append(Mark("DNS", "подменён", WARN, "fa5s.exchange-alt"))
    elif marks:
        clean = bool(dns_states) and not dns_states & {"", "unknown"}
        marks.append(Mark("DNS", "честный" if clean else "—", OK if clean else UNKNOWN, "fa5s.exchange-alt"))
    if service.get("dns_note"):
        chips.append(("DNS подменён", WARN))
    if any(item.get("hosts_stale") for item in targets):
        tags.append(("запись в hosts устарела", WARN))
    if any((item.get("registry") or {}).get("listed") for item in targets):
        tags.append(("в реестре РКН", INFO))
    if service.get("control"):
        tags.append(("контрольный", INFO))
    chips.extend(tags)

    detail: list[Section] = []
    for item in targets:
        rows = [Line(_target_state(item), "Соединение", str(item.get("text") or ""))]
        if item.get("address"):
            rows.append(Line(INFO, "Адрес сервера", str(item["address"])))
        tried = list(item.get("tried") or ())
        if len(tried) > 1 or (tried and not item.get("ok")):
            rows.append(
                Line(
                    INFO,
                    "Какие адреса пробовали",
                    ", ".join(f"{step.get('address', '')} — {_TRIED_WORDS.get(str(step.get('result')), 'сбой')}" for step in tried),
                )
            )
        if item.get("rechecked") == "opened":
            rows.append(Line(INFO, "Повторная проверка", "открылся со второго раза, поодиночке — первый сбой дала нагрузка самой проверки"))
        elif item.get("rechecked") == "same":
            rows.append(Line(INFO, "Повторная проверка", "поодиночке, когда остальные проверки закончились, — результат тот же"))
        if item.get("hosts_stale"):
            rows.append(
                Line(
                    WARN,
                    "Файл hosts",
                    "записанный в нём адрес не работает. Проверка открыла сайт по настоящему адресу, "
                    "а браузер пойдёт по записи и сайт не откроет — обновите её в «Редакторе hosts»",
                )
            )
        cert = item.get("cert") or {}
        if cert:
            rows.append(Line(FAIL, "Чужой сертификат", str(cert.get("text") or "")))
            if cert.get("issuer"):
                rows.append(Line(INFO, "Кем выдан", str(cert["issuer"])))
            if cert.get("names"):
                rows.append(Line(INFO, "Каким сайтам выдан", ", ".join(map(str, cert["names"][:6]))))
            if cert.get("advice"):
                rows.append(Line(INFO, "Что делать", str(cert["advice"])))
        for proto in item.get("protocols") or ():
            rows.append(
                Line(_PROTOCOL_STATES.get(str(proto.get("state")), UNKNOWN), str(proto.get("title") or ""), str(proto.get("text") or ""))
            )
        if item.get("cause_text"):
            rows.append(Line(FAIL, "Как блокируют", str(item["cause_text"])))
        if "registry" in item:
            rows.append(Line(INFO, "Реестр РКН", _registry_text(item)))
        if item.get("seconds"):
            from diagnostics.report_text import stages_text

            parts = stages_text(item.get("stages"))
            rows.append(Line(INFO, "Проверка заняла", f"{float(item['seconds']):.0f} с" + (f" ({parts})" if parts else "")))
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
        # Дороги показывают, только когда главный адрес проверяли по протоколам отдельно.
        marks=tuple(marks) if main.get("protocols") else (),
        tags=tuple(tags),
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
    burst = voice.get("burst") or {}
    if burst.get("text"):
        burst_state = {"ok": OK, "freeze": WARN}.get(str(burst.get("state")), UNKNOWN)
        rows = [Line(burst_state, "Тридцать пакетов подряд", _capital(str(burst["text"])))]
        rows += [Line(INFO, str(item.get("name") or ""), "ответов: " + ", ".join(item.get("series") or ())) for item in burst.get("servers") or ()]
        sections.append(Section("Не замирает ли UDP", tuple(rows)))
        if burst_state == WARN:
            lines = lines + (Line(WARN, "UDP замирает", _capital(str(burst["text"]))),)
            level = WARN if level == OK else level
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


def _telegram_card(telegram: dict) -> Card:
    items = list(telegram.get("items") or ())
    level = _state(telegram.get("level"))
    lines = tuple(
        Line(
            _SERVER_STATE.get(str(item.get("state")), UNKNOWN),
            f"{item.get('name', '')} · {item.get('address', '')}",
            str(item.get("text") or ""),
        )
        for item in items
    )
    answered = sum(1 for item in items if item.get("state") == "ok")
    sections = [
        Section(str(telegram.get("headline") or "Дата-центры Telegram"), lines),
        Section(
            "Что это за проверка",
            (
                Line(
                    INFO,
                    "Приложение Telegram ходит не на сайт, а прямо по адресам своих дата-центров. "
                    "Здесь с каждым из них устанавливается обычное соединение; неудачное повторяется после паузы.",
                ),
            ),
        ),
    ]
    advice = [str(item) for item in telegram.get("advice") or ()]
    if advice:
        sections.append(Section("Что делать", tuple(Line(INFO, item) for item in advice)))
    return Card(
        key="telegram",
        icon="fa5b.telegram-plane",
        title="Telegram: дата-центры",
        level=level,
        status=f"Отвечают {answered} из {len(items)}" if items else "Не проверено",
        lines=lines,
        sections=tuple(sections),
    )


_SPEED_STATUS = {OK: "Разницы нет", WARN: "Зарубежные медленнее", UNKNOWN: "Не удалось сравнить"}


def _name_speed_sections(names: dict) -> tuple[Section, ...]:
    """Замедление по имени сайта: скорость одного сервера с его именем и с именами проверенных сайтов."""
    rows = tuple(
        Line(_state(item.get("state")), f"{item.get('name', '')} ({item.get('host', '')})", str(item.get("text") or ""))
        for item in names.get("items") or ()
    )
    if not rows:
        return ()
    control = Line(INFO, f"Контроль: {names.get('server', '')} под своим именем", str(names.get("control") or ""))
    return (
        Section(str(names.get("headline") or "Замедление по имени сайта"), (control, *rows)),
        Section(
            "Как проверяется замедление по имени",
            (
                Line(
                    INFO,
                    "С одного и того же постороннего сервера качается один файл: сначала под его собственным именем, "
                    "затем под именем каждого открывшегося сайта. Сервер и дорога те же, отличается только имя, которое "
                    "видит фильтр. Если с именем сайта скорость в разы ниже — замедляют по имени. Медленный замер "
                    "перепроверяется, контроль в конце повторяется.",
                ),
            ),
        ),
    )


def _speed_card(speed: dict) -> Card:
    names = speed.get("names") or {}
    slow_names = [str(item.get("name") or "") for item in names.get("items") or () if item.get("state") == "warn"]
    if slow_names:
        return replace(
            _speed_card({**speed, "names": None}),
            level=WARN,
            status=f"Замедляют: {', '.join(slow_names)}",
            sections=(*_name_speed_sections(names), *_speed_card({**speed, "names": None}).sections),
        )
    level = _state(speed.get("level"))
    lines = tuple(
        Line(
            _state(item.get("state"), INFO),
            f"{item.get('name', '')} (Россия)" if item.get("domestic") else str(item.get("name") or ""),
            str(item.get("text") or ""),
        )
        for item in speed.get("items") or ()
    )
    return Card(
        key="speed",
        icon="fa5s.tachometer-alt",
        title="Скорость",
        level=level,
        status=_SPEED_STATUS.get(level, "Не проверено"),
        lines=lines,
        sections=(
            Section(str(speed.get("headline") or "Скорость загрузки"), lines),
            *_name_speed_sections(names),
            Section(
                "Что это за проверка",
                (
                    Line(
                        INFO,
                        "С каждого сервера качается два-три мегабайта, не дольше нескольких секунд. Сама цифра "
                        "зависит от тарифа, поэтому зарубежные серверы сравниваются с российскими. "
                        "«Медленнее» говорится, только когда медленны все зарубежные сразу и разница — в разы.",
                    ),
                ),
            ),
        ),
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
        Line(_state(item.get("level"), INFO), str(item.get("text") or ""), parts=read_finding_parts(item))
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
    """«Место фильтра»: вывод, срок жизни для стратегий, сайты, по которым искали, и вся дорога с владельцами узлов."""
    found = bool(place.get("found"))
    state = str(place.get("state") or ("found" if found else "not_found"))
    text = str(place.get("text") or "")
    hop = place.get("hop") if found else None
    level = WARN if found or state in ("disagree", "disturbed") else (INFO if state == "none" else UNKNOWN)
    status = {
        "found": "В роутере или на компьютере" if hop == 1 else f"Между узлами {(hop or 1) - 1} и {hop}",
        "disagree": "Узлы не совпали",
        "disturbed": "Мешает программа обхода",
        "none": "Блокировок по имени нет",
    }.get(state, "Не найдено")
    # Одного места нет, но по отдельным способам блокировки оно найдено: это и есть главный итог.
    located = [item for item in place.get("mechanisms") or () if item.get("hop")]
    if not found and located:
        hops = sorted({int(item["hop"]) for item in located})
        level = WARN
        status = f"По способам: {'узел' if len(hops) == 1 else 'узлы'} {', '.join(map(str, hops))}"
    preview = [Line(WARN, str(item.get("title") or ""), _capital(str(item.get("text") or ""))) for item in located] if not found else []
    preview.append(Line(level, _capital(text)))
    if place.get("ttl_advice"):
        preview.append(Line(INFO, "Для стратегий", str(place["ttl_advice"])))
    sites = list(place.get("sites") or ())
    if sites:
        preview.append(Line(INFO, "Искали по сайтам", ", ".join(str(item.get("host") or "") for item in sites)))
    elif place.get("host"):
        preview.append(Line(INFO, "Дорога показана до", str(place["host"])))

    sections = [Section("Вывод", tuple(line for line in preview if not line.text or line.name == "Для стратегий")[:2])]
    mechanisms = [
        Line(WARN if item.get("hop") else UNKNOWN, str(item.get("title") or ""), _capital(str(item.get("text") or "")))
        for item in place.get("mechanisms") or ()
    ]
    if mechanisms:
        if place.get("same_place"):
            mechanisms.append(Line(INFO, "Один фильтр или несколько", str(place["same_place"])))
        sections.append(Section("Чем и где режут", tuple(mechanisms)))
    unmeasured = [Line(INFO, str(item.get("title") or ""), str(item.get("text") or "")) for item in place.get("unmeasured") or ()]
    if unmeasured and mechanisms:
        sections.append(Section("Место не измеряется", tuple(unmeasured)))
    site_rows: list[Line] = []
    for item in sites:
        distance = f" · до сервера {item['distance']} узлов" if item.get("distance") else ""
        site_rows.append(
            Line(
                # Найденное по сайту — свидетельство, а не отдельное замечание: замечание одно, в выводе.
                INFO if item.get("found") else UNKNOWN,
                f"{item.get('host', '')} ({item.get('address', '')}{distance})",
                _capital(str(item.get("text") or "")),
            )
        )
        for method in item.get("methods") or ():
            site_rows.append(Line(INFO, f"   способ {method.get('title', '')}", str(method.get("text") or "")))
    if site_rows:
        sections.append(Section("По каким сайтам искали", tuple(site_rows)))
    if place.get("reasons"):
        sections.append(Section("На чём основан вывод", tuple(Line(INFO, str(reason)) for reason in place["reasons"])))
    hops = []
    for item in place.get("hops") or ():
        if item.get("ttl") == hop:
            hops.append(Line(FAIL, f"── {FILTER_MARK} ──"))
        answered = bool(item.get("address"))
        hops.append(
            Line(
                INFO,
                f"Узел {item.get('ttl', '')}",
                " · ".join(
                    part
                    for part in (
                        str(item.get("address") or "не ответил"),
                        _hop_time(item.get("rtt_ms")),
                        str(item.get("owner") or "") if answered else "",
                    )
                    if part
                ),
            )
        )
    if hops:
        sections.append(Section(f"Дорога до {place.get('host', '')} ({place.get('address', '')})", tuple(hops)))
    return Card(
        key="filter",
        icon="fa5s.route",
        title="Место фильтра",
        level=level,
        status=status,
        lines=tuple(preview),
        sections=tuple(sections),
    )


_WAY_STATES = {"passed": OK, "cut": FAIL}
_HABIT_STATUS = {
    "tcp": "Не склеивает пакеты",
    "timer": "Склеивает только подряд",
    "records": "Не разбирает записи TLS",
    "none": "Собирает приветствие целиком",
    "not_by_name": "По имени не режет",
}


def _habits_card(habits: dict) -> Card:
    """«Как работает фильтр»: какое дробление приветствия проходит и режется ли ECH."""
    ech = habits.get("ech") or {}
    ech_state = {"blocked": WARN, "fine": OK}.get(str(ech.get("state")), UNKNOWN)
    from diagnostics.filter_habits import NOT_RUN, way_text

    disturbed = [str(name) for name in habits.get("disturbed") or ()]
    lines = [Line(WARN if disturbed else INFO, _capital(str(habits.get("headline") or "")))]
    if habits.get("advice"):
        lines.append(Line(INFO, "Что делать" if disturbed else "Какие стратегии пробовать", str(habits["advice"])))
    if ech.get("text"):
        lines.append(Line(ech_state, "ECH", _capital(str(ech["text"]))))
    sections = [Section("Что делает фильтр", tuple(lines[:2]))]
    for site in habits.get("sites") or ():
        rows = [Line(INFO, "Вывод", _capital(str(site.get("text") or "")))]
        if site.get("advice"):
            rows.append(Line(INFO, "Какие стратегии пробовать", str(site["advice"])))
        for way in site.get("ways") or ():
            if way.get("state") == NOT_RUN:
                continue
            # Строка способа: что вышло с именем сайта, что с безобидным именем и что это значит.
            rows.append(Line(_WAY_STATES.get(str(way.get("state")), UNKNOWN), str(way.get("title") or ""), way_text(way)))
        sections.append(Section(f"{site.get('host', '')} ({site.get('address', '')})", tuple(rows)))
    if habits.get("sites") and habits.get("limits"):
        sections.append(Section("Чего эта проверка не видит", (Line(INFO, str(habits["limits"])),)))
    if ech.get("text"):
        rows = [Line(ech_state, "Шифрованное имя сайта (ECH)", _capital(str(ech["text"])))]
        if ech.get("advice"):
            rows.append(Line(INFO, "Что делать", str(ech["advice"])))
        sections.append(Section("Cloudflare и ECH", tuple(rows)))
    return Card(
        key="habits",
        icon="fa5s.cut",
        title="Как работает фильтр",
        level=WARN if ech_state == WARN or disturbed else INFO,
        status=_habits_status(habits, disturbed, ech_state),
        lines=tuple(lines),
        sections=tuple(sections),
    )


def _habits_status(habits: dict, disturbed: list[str], ech_state: str) -> str:
    """Слово результата на карточке: вывод о фильтре, если он один на все сайты."""
    if disturbed:
        return "Мешает обход"
    codes = {str(site.get("code")) for site in habits.get("sites") or ()} - {"unknown"}
    if len(codes) == 1:
        return _HABIT_STATUS.get(codes.pop(), "Проверено дробление")
    if len(codes) > 1:
        return "По сайтам по-разному"
    if habits.get("sites"):
        return "Вывода нет"
    return "ECH режется" if ech_state == WARN else "ECH проходит"


def _network_card(network: dict) -> Card:
    lines = tuple(
        Line(_state(item.get("state"), INFO), str(item.get("name") or ""), str(item.get("text") or ""))
        for item in network.get("lines") or ()
    )
    provider = str(network.get("provider") or "")
    level = WARN if any(line.state == WARN for line in lines) else (INFO if network.get("external_ip") else UNKNOWN)
    return Card(
        key="network",
        icon="fa5s.wifi",
        title="Ваша сеть",
        level=level,
        status=provider or ("Провайдер не определён" if network.get("external_ip") else "Не удалось узнать"),
        lines=lines,
        sections=(
            Section("Под каким адресом вас видит интернет", lines),
            Section(
                "Зачем это",
                (
                    Line(
                        INFO,
                        "Блокировки у провайдеров разные. Если здесь чужая сеть (VPN, прокси), "
                        "вся проверка описывает сеть вместе с ней, а не вашего провайдера.",
                    ),
                ),
            ),
        ),
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
    if report.get("network"):
        cards.append(_network_card(report["network"]))
    freeze = report.get("freeze")
    if freeze and freeze.get("servers"):
        cards.append(_hostings_card(freeze))
    if report.get("voice"):
        cards.append(_voice_card(report["voice"]))
    if report.get("telegram"):
        cards.append(_telegram_card(report["telegram"]))
    dns = _dns_card(report)
    if dns is not None:
        cards.append(dns)
    if report.get("dns_servers"):
        cards.append(_dns_servers_card(report["dns_servers"]))
    if report.get("speed"):
        cards.append(_speed_card(report["speed"]))
    if report.get("ipv6"):
        cards.append(_ipv6_card(report["ipv6"]))
    if report.get("filter"):
        cards.append(_filter_card(report["filter"]))
    if report.get("habits"):
        cards.append(_habits_card(report["habits"]))
    system = list(report.get("system") or ())
    if system:
        cards.append(_system_card(system))
    if report.get("compare"):
        cards.append(_compare_card(report["compare"], report.get("services") or ()))
    run = _run_card(report)
    if run is not None:
        cards.append(run)
    return cards


_COMPARE_STATES = {"helped": OK, "not_helped": FAIL, "broken": WARN, "fine_anyway": INFO}
_COMPARE_STATUS = {OK: "Обход справляется", WARN: "Помогает не всем", FAIL: "Пресет не помог"}


# Что было с сайтом в двух проверках — по группе сравнения: (с Zapret, без Zapret).
_COMPARE_OPENS = {
    "helped": ("открывается", "не открывается"),
    "not_helped": ("не открывается", "не открывается"),
    "broken": ("не открывается", "открывается"),
    "fine_anyway": ("открывается", "открывается"),
}


def _one_preset_note(preset: str) -> str:
    """Оговорка, без которой сравнение читают неверно: это итог одного пресета, а не Zapret вообще."""
    name = f"«{preset}»" if preset else "выбранного сейчас"
    return (
        f"Это итог одного пресета — {name}. Пресет решает, для каких сайтов и какой обход включён: "
        "с другим пресетом или после «Подбора стратегии» результат может быть другим."
    )


def _compare_site_page(name: str, key: str, compare: dict, services) -> Card:
    """Страница сайта из сравнения: что было с Zapret и без, и все пробы этой проверки."""
    from diagnostics.compare import GROUP_TITLES
    from diagnostics.history import format_time

    preset = str(compare.get("preset") or "")
    with_zapret, without = _COMPARE_OPENS[key]
    now, past = ("С Zapret", "Без Zapret") if compare.get("zapret_in") == "current" else ("Без Zapret", "С Zapret")
    now_text, past_text = (with_zapret, without) if compare.get("zapret_in") == "current" else (without, with_zapret)
    state = _COMPARE_STATES[key]
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
            Line(INFO, _one_preset_note(preset)),
        ),
    )
    service = next((item for item in services if str(item.get("label") or "") == name), None)
    if service is None:
        # Сайта нет в этой проверке (другой набор сайтов): остаётся само сравнение.
        return Card(f"compare:{name}", "fa5s.globe", name, state, GROUP_TITLES[key], sections=(verdict,), site=True)
    site = _site_card(service)
    # Уровень остаётся от самой проверки сайта: по нему рисуется дорога и место, где режут.
    return replace(site, key=f"compare:{site.key}", status=GROUP_TITLES[key], kind="", sections=(verdict, *site.sections))


def _compare_card(compare: dict, services=()) -> Card:
    """«С Zapret и без»: что дала пара проверок — эта и прошлая в противоположном состоянии обхода."""
    from diagnostics.compare import GROUP_TITLES
    from diagnostics.history import format_time

    level = _state(compare.get("level"))
    preset = str(compare.get("preset") or "")
    groups = [(key, [str(name) for name in compare.get(key) or ()]) for key in _COMPARE_STATES]
    summary = tuple(
        Line(_COMPARE_STATES[key], GROUP_TITLES[key], ", ".join(names)) for key, names in groups if names
    )
    other = "без Zapret" if compare.get("zapret_in") == "current" else "с Zapret"
    facts = [Line(INFO, f"Прошлая проверка {other}", format_time(str(compare.get("other_time") or "")))]
    if compare.get("preset"):
        facts.append(Line(INFO, "Пресет", str(compare["preset"])))
    # Первой строкой — что это итог одного пресета: без этого «не помог» читают как «Zapret не поможет».
    sections = [Section(str(compare.get("headline") or "С Zapret и без"), (Line(INFO, _one_preset_note(preset)), *summary))]
    sections += [
        Section(
            GROUP_TITLES[key],
            # Плитка сайта открывает его страницу: что было в обеих проверках и все пробы этой.
            tuple(Line(_COMPARE_STATES[key], name, page=_compare_site_page(name, key, compare, services)) for name in names),
            tiles=True,
        )
        for key, names in groups
        if names
    ]
    sections.append(Section("Что сравнивали", tuple(facts)))
    notes = tuple(Line(INFO, str(note)) for note in compare.get("notes") or ())
    if notes:
        sections.append(Section("Как это читать", notes))
    return Card(
        key="compare",
        icon="fa5s.balance-scale",
        title="С Zapret и без",
        level=level,
        # В слове итога назван пресет: сравнение говорит о нём одном.
        status=" · ".join(part for part in (_COMPARE_STATUS.get(level, "Сравнение"), f"пресет «{preset}»" if preset else "") if part),
        lines=summary,
        sections=tuple(sections),
    )


def _registry_lines(summary: dict) -> tuple[Line, ...]:
    """Сам список реестра РКН, по которому ставятся отметки «в реестре»: свежий ли и сколько в нём записей."""
    import time

    state = str(summary.get("state") or "")
    if not state:
        return ()
    if not summary.get("hosts"):
        return (Line(UNKNOWN, "Список", "скачать не удалось — отметок «в реестре» в этой проверке нет"),)
    when = time.strftime("%d.%m.%Y", time.localtime(float(summary.get("updated") or 0)))
    fresh = state == "fresh"
    return (
        Line(OK if fresh else WARN, "Скачан", when if fresh else f"{when} — давно не обновлялся, свежий скачать не удалось"),
        Line(INFO, "Имён сайтов", f"{int(summary['hosts']):,}".replace(",", " ")),
        Line(INFO, "Адресов и сетей", f"{int(summary.get('networks') or 0):,}".replace(",", " ")),
    )


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
    listing = _registry_lines(report.get("registry") or {})
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
