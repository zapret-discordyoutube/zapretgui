"""Итог «Проверки домена» карточками: по одной на каждую часть проверки.

На вкладке видно до пяти карточек — как открывается сам сайт (те же пробы, что в
BlockCheck), «Пинг и сеть», «Путь до сервера», «Адреса с разных DNS-серверов»,
«Кто ещё на этом адресе»: значок, одно слово итога и
пара главных строк. Всё остальное (десятки DNS-серверов, узлы дороги, списки
доменов) лежит на странице подробностей, которую открывает нажатие на карточку.
Так на вкладке остаётся несколько виджетов вместо сотен строк.
"""

from __future__ import annotations

from dataclasses import replace

import dns.domain_lookup_plans as plans
from blockcheck.ui.result_cards_model import Card, Line, Section

# Карточка «как открывается сайт» приходит с ключом сайта (``site:user:<домен>``); здесь — ключ её названия.
KEY_SITE = "lookup:site"
KEY_PING = "lookup:ping"
KEY_PATH = "lookup:path"
KEY_DNS = "lookup:dns"
KEY_NEIGHBORS = "lookup:neighbors"

TITLES = {
    KEY_SITE: "{domain}",
    KEY_PING: "Пинг и сеть",
    KEY_PATH: "Путь до сервера",
    KEY_DNS: "Адреса с разных DNS-серверов",
    KEY_NEIGHBORS: "Кто ещё на этом адресе",
}

_TONE_STATES = {
    plans.TONE_SUCCESS: "ok",
    plans.TONE_WARNING: "warn",
    plans.TONE_ERROR: "fail",
    plans.TONE_ACCENT: "info",
    plans.TONE_MUTED: "info",
}
# Подпись длиннее — это уже фраза, а не «что измеряли».
_NAME_LIMIT = 46


def _line(info: plans.InfoLine) -> Line:
    """Фраза «Пинг 1.2.3.4: нет ответа» → подпись и значение; фраза без двоеточия остаётся целой."""
    state = _TONE_STATES.get(info.tone, "info")
    head, separator, rest = info.text.partition(": ")
    if separator and len(head) <= _NAME_LIMIT:
        return Line(state, head, rest.rstrip("."))
    return Line(state, info.text)


def _worst(lines) -> str:
    states = {line.state for line in lines}
    return next((state for state in ("fail", "warn", "ok") if state in states), "unknown")


def _ping_card(report, title: str) -> Card | None:
    ping = tuple(_line(line) for line in plans.build_ping_lines(report))
    if not ping:
        return None
    network: list[Line] = []
    for info in plans.build_network_lines(report):
        if info.tone == plans.TONE_ACCENT:
            network.append(_line(info))
            continue
        # «ASN: AS1, подсеть: 1.2.3.0/24, страна: RU» — по строке на каждое.
        for part in info.text.split(", "):
            head, _separator, rest = part.partition(": ")
            network.append(Line("info", f"{head[:1].upper()}{head[1:]}", rest))
    states = {line.state for line in ping}
    if "ok" in states:
        level, status = "ok", "Сервер отвечает"
    elif "warn" in states:
        level, status = "warn", "Сервер не отвечает"
    else:
        level, status = "unknown", "Проверяем…"
    sections = [Section("Пинг и подключение", ping)]
    if network:
        sections.append(Section("Сеть адреса", tuple(network)))
    return Card(KEY_PING, "fa5s.signal", title, level, status, lines=(*ping, *network[:1]), sections=tuple(sections))


def _path_card(report, title: str) -> Card | None:
    notes = tuple(Line(_TONE_STATES.get(info.tone, "info"), info.text) for info in plans.build_path_lines(report))
    hops = tuple(Line(row.state, row.name, row.text) for row in plans.build_path_rows(report))
    if not notes and not hops:
        return None
    count = sum(1 for row in hops if row.text)
    filtered = any(row.state == "fail" for row in (*notes, *hops))
    status = "Найден фильтр по дороге" if filtered else (f"Узлов по дороге: {count}" if count else "Узлы не ответили")
    sections = [Section("Что видно по дороге", notes)] if notes else []
    if hops:
        sections.append(Section("Узлы по дороге до сервера", hops, tiles=True, tile_icon="fa5s.network-wired"))
    return Card(
        KEY_PATH, "fa5s.route", title, "fail" if filtered else _worst(notes), status, lines=notes, sections=tuple(sections)
    )


def _dns_card(report, title: str) -> Card | None:
    groups = plans.build_answer_groups(report)
    if not groups:
        return None
    summary = plans.build_dns_summary(report)
    state = _TONE_STATES.get(summary.tone, "info")
    status, _separator, rest = summary.text.partition(". ")
    pages = [_answer_page(row) for row in plans.build_answer_rows(report)]
    answers = tuple(Line(row.state, row.name, row.text, page=page) for row, page in zip(groups[0].rows, pages))
    # На карточке — только серверы, ответившие не как все: остальные десятки строк — в подробностях.
    odd = tuple(line for line in answers if line.state in ("fail", "warn"))
    lines = ((Line(state, rest),) if rest else ()) + odd
    sections = (
        Section("Итог", (Line(state, summary.text),)),
        Section(groups[0].title, answers, tiles=True),
    )
    level = state if state in ("ok", "warn", "fail") else "unknown"
    return Card(KEY_DNS, "fa5s.network-wired", title, level, status.rstrip("."), lines=lines, sections=sections)


_ANSWER_LEVELS = {plans.LEVEL_OK: "ok", plans.LEVEL_WARN: "warn", plans.LEVEL_FAIL: "fail"}


def _answer_page(row: plans.AnswerRow) -> Card:
    """Страница одного DNS-сервера: каким способом спрашивали и что он ответил."""
    level = _ANSWER_LEVELS.get(row.level, "unknown")
    lines: list[Line] = []
    # Первая строка подсказки — название сервера: оно уже в заголовке страницы.
    for text in row.tooltip.split("\n")[1:]:
        if text.startswith("Сервер "):
            address, _separator, way = text.removeprefix("Сервер ").partition(", ")
            lines.append(Line("info", "Адрес сервера", address))
            if way:
                lines.append(Line("info", "Способ запроса", way))
            continue
        head, separator, rest = text.partition(": ")
        lines.append(Line("info", head, rest) if separator else Line("info", text))
    if row.time:
        lines.append(Line("info", "Время ответа", row.time))
    return Card(
        key=f"lookup:answer:{row.server}:{row.address}",
        icon="fa5s.network-wired",
        title=row.server,
        level=level,
        status=row.result,
        sections=(Section("Что ответил сервер", (Line(level if level != "unknown" else "unknown", row.result), *lines)),),
    )


def _site_card(report, title: str) -> Card | None:
    """Как сайт открывается — та же карточка, что у сайта в BlockCheck: дороги TLS, HTTP, QUIC и способ блокировки."""
    if not report.site:
        return None
    from blockcheck.ui.result_cards_model import site_card

    return replace(site_card(report.site), title=title)


def _neighbors_card(report, title: str) -> Card | None:
    groups = plans.build_neighbor_groups(report)
    if not groups:
        return None
    sources: list[Line] = []
    sections: list[Section] = []
    for group in groups:
        head, names = group.rows[0], group.rows[1:]
        sources.append(Line("ok" if names else "unknown", group.title, head.name))
        if names:
            rows = tuple(Line(row.state, row.name, row.text) for row in names)
            sections.append(Section(group.title, rows, tiles=True, tile_icon="fa5s.globe"))
    found = plans.count_neighbors(report)
    status = f"Найдено доменов: {found}" if found else "Других доменов не найдено"
    sections.insert(0, Section("Где искали", tuple(sources)))
    return Card(
        KEY_NEIGHBORS,
        "fa5s.sitemap",
        title,
        "ok" if found else "unknown",
        status,
        lines=tuple(sources),
        sections=tuple(sections),
    )


def build_lookup_cards(report, titles: dict[str, str] | None = None) -> list[Card]:
    """Карточки итога в порядке показа; части, о которых пока нечего сказать, пропускаются."""
    names = {**TITLES, **(titles or {})}
    built = (
        _site_card(report, names[KEY_SITE].format(domain=report.target)),
        _ping_card(report, names[KEY_PING]),
        _path_card(report, names[KEY_PATH]),
        _dns_card(report, names[KEY_DNS]),
        _neighbors_card(report, names[KEY_NEIGHBORS]),
    )
    return [card for card in built if card is not None]


__all__ = ["KEY_DNS", "KEY_NEIGHBORS", "KEY_PATH", "KEY_PING", "KEY_SITE", "TITLES", "build_lookup_cards"]
