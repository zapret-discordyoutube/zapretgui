"""Как показывать отчёт проверки DNS-серверов: карточки серверов, строки адресов, итог и текст.

Чистые функции над ``dns.server_check.ServerCheckReport``: ни сети, ни Qt.
"""

from __future__ import annotations

from dataclasses import dataclass

from dns.domain_lookup_plans import (
    TONE_ACCENT,
    TONE_ERROR,
    TONE_MUTED,
    TONE_SUCCESS,
    TONE_WARNING,
    InfoLine,
)
from dns.server_check import (
    CODE_DEAD,
    CODE_DOH_BLOCKED,
    CODE_DOH_NAME_BLOCKED,
    CODE_FOREIGN_ANSWERS,
    CODE_INTERCEPTED,
    CODE_SELF_FILTER,
    CODE_SPOOFED,
    CODE_DOT_BLOCKED,
    CODE_TCP_BLOCKED,
    CODE_UDP_BLOCKED,
    CODE_UNSTABLE,
    LEVEL_FAIL,
    LEVEL_INFO,
    LEVEL_OK,
    LEVEL_WARN,
    STATE_FAIL,
    STATE_OK,
    STATE_SKIP,
    TRANSPORT_ICMP,
    TRANSPORTS,
    Cell,
    Observation,
    ServerCheckReport,
)
from utils.dns_wire import (
    FAILURE_CERT,
    FAILURE_CLOSED,
    FAILURE_HTTP,
    FAILURE_MALFORMED,
    FAILURE_REFUSED,
    FAILURE_RESET,
    FAILURE_TIMEOUT,
    FAILURE_TLS,
    FAILURE_UNREACHABLE,
    TRANSPORT_DOH,
    TRANSPORT_DOT,
    TRANSPORT_TCP,
    TRANSPORT_UDP,
)

TRANSPORT_TITLES = {
    TRANSPORT_ICMP: "Пинг",
    TRANSPORT_UDP: "UDP 53",
    TRANSPORT_TCP: "TCP 53",
    TRANSPORT_DOT: "DoT 853",
    TRANSPORT_DOH: "DoH 443",
}

CELL_OK = "ok"
# Отвечает, но не на каждый запрос.
CELL_WARN = "warn"
CELL_FAIL = "fail"
CELL_MUTED = "muted"

# Коротко, чтобы помещалось в ячейку; полная причина — в подсказке строки.
_FAILURE_WORDS = {
    FAILURE_TIMEOUT: "молчит",
    FAILURE_REFUSED: "порт закрыт",
    FAILURE_RESET: "обрыв",
    FAILURE_CLOSED: "обрыв",
    FAILURE_UNREACHABLE: "нет дороги",
    FAILURE_TLS: "нет шифрования",
    FAILURE_CERT: "чужой сертификат",
    FAILURE_HTTP: "ошибка сервера",
    FAILURE_MALFORMED: "не DNS",
}
_LEVEL_TONES = {LEVEL_OK: TONE_SUCCESS, LEVEL_INFO: TONE_MUTED, LEVEL_WARN: TONE_WARNING, LEVEL_FAIL: TONE_ERROR}
_LEVEL_ORDER = {LEVEL_FAIL: 0, LEVEL_WARN: 1, LEVEL_INFO: 2, LEVEL_OK: 3}
_LEVEL_MARKS = {LEVEL_FAIL: "✗", LEVEL_WARN: "!", LEVEL_INFO: "·", LEVEL_OK: "✓"}


# Эти выводы уже видны в ячейках способов связи: в столбце замечаний они
# только повторяли бы таблицу. В подсказке строки и в отчёте они остаются.
_SHOWN_IN_CELLS = frozenset(
    {CODE_UDP_BLOCKED, CODE_TCP_BLOCKED, CODE_DOT_BLOCKED, CODE_DOH_BLOCKED, CODE_UNSTABLE}
)


# В столбце замечаний — два-три слова, чтобы текст помещался целиком.
# Полная фраза с подробностями остаётся в подсказке строки и в отчёте.
_SHORT_NOTES = {
    CODE_SPOOFED: "Ответы подменяются",
    CODE_FOREIGN_ANSWERS: "Отвечает чужая сеть",
    CODE_DOH_NAME_BLOCKED: "DoH закрыт по имени",
    CODE_DEAD: "Не отвечает",
    CODE_SELF_FILTER: "Сам не отдаёт часть сайтов",
}
# На карточке сервера ячеек по способам связи не видно, поэтому там называем и их.
_CARD_NOTES = {
    **_SHORT_NOTES,
    CODE_UDP_BLOCKED: "Обычный DNS закрыт",
    CODE_DOT_BLOCKED: "DoT закрыт",
    CODE_DOH_BLOCKED: "DoH закрыт",
    CODE_UNSTABLE: "Отвечает через раз",
}

# Что с сервером в целом — от этого зависят цвет карточки и её место в списке.
CARD_NETWORK = "network"
CARD_SELF = "self"
CARD_PARTIAL = "partial"
CARD_OK = "ok"
CARD_SILENT = "silent"
CARD_ORDER = (CARD_NETWORK, CARD_PARTIAL, CARD_SELF, CARD_OK, CARD_SILENT)
CARD_TITLES = {
    CARD_NETWORK: "Блокируется по дороге",
    CARD_SELF: "Сам не отдаёт часть сайтов",
    CARD_PARTIAL: "Работает не полностью",
    CARD_OK: "Работает",
    CARD_SILENT: "Не отвечает",
}
# Короткие подписи для фильтра и полосы «насколько всё плохо».
CARD_GROUPS = {
    CARD_NETWORK: "Блокируются",
    CARD_SELF: "Сами фильтруют",
    CARD_PARTIAL: "Не полностью",
    CARD_OK: "Работают",
    CARD_SILENT: "Молчат",
}
CARD_HINTS = {
    CARD_NETWORK: (
        "Мешает сеть — провайдер или его оборудование. Это доказано: сервер по обычному пути противоречит сам "
        "себе, либо закрыто именно его имя, либо за него отвечает чужая сеть."
    ),
    CARD_SELF: "Так решил сам сервер: он не отдаёт эти сайты даже по шифрованному пути, который по дороге не подменить.",
    CARD_PARTIAL: (
        "Отвечает не всеми способами или через раз. Закрыть способ связи мог и провайдер, и сам сервер — "
        "по одной проверке это не отличить. Если так же закрыто у многих разных серверов, дело в провайдере."
    ),
    CARD_OK: "Отвечает всеми способами, ответы никто не подменяет.",
    CARD_SILENT: "Не отвечает ни одним способом: сервер выключен или закрыт для вашей сети целиком.",
}
# Находки, в которых сеть по дороге виновата доказанно: сервер сам себе противоречит
# или без имени тот же адрес отвечает.
_NETWORK_CODES = frozenset({CODE_SPOOFED, CODE_DOH_NAME_BLOCKED})
# Способ связи закрыт, но кем — провайдером или самим сервером — по фактам не видно.
_CLOSED_CODES = frozenset({CODE_DOH_BLOCKED, CODE_DOT_BLOCKED, CODE_UDP_BLOCKED})


@dataclass(frozen=True, slots=True)
class ServerRow:
    server: str
    address: str
    cells: tuple[str, ...]
    cell_levels: tuple[str, ...]
    note: str
    # Важность показанного замечания — для его цвета.
    note_level: str
    # Важность строки целиком, с учётом закрытых способов связи.
    level: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class ServerCard:
    """Один сервер целиком: все его адреса и общий вывод."""

    server: str
    status: str
    # Коротко, что не так; пусто — замечаний нет.
    note: str
    # Лучшее время шифрованного запроса (DoH), а если его нет — обычного.
    best: str
    # По каждому способу связи — состояния по адресам (CELL_*), для точек на карточке.
    dots: tuple[tuple[str, ...], ...]
    addresses: tuple[ServerRow, ...]
    tooltip: str

    @property
    def spoken(self) -> str:
        parts = [self.server, CARD_TITLES[self.status], self.note, self.best, f"адресов: {len(self.addresses)}"]
        return ". ".join(part for part in parts if part)


def format_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return "< 1 мс" if value < 1 else f"{round(value)} мс"


def cell_text(transport: str, cell: Cell) -> str:
    if cell.state == STATE_OK:
        if cell.unstable:
            return f"{format_ms(cell.elapsed_ms)} · {cell.answered} из {cell.attempts}"
        return format_ms(cell.elapsed_ms)
    if cell.state == STATE_SKIP:
        return "—"
    if cell.state == STATE_FAIL:
        if transport == TRANSPORT_ICMP:
            return "нет ответа"
        return _FAILURE_WORDS.get(cell.failure, "не отвечает")
    return "…"


def cell_level(transport: str, cell: Cell) -> str:
    if cell.state == STATE_OK:
        return CELL_WARN if cell.unstable else CELL_OK
    # Молчание на пинг — обычное дело, красным его красить нельзя.
    if cell.state == STATE_FAIL and transport != TRANSPORT_ICMP:
        return CELL_FAIL
    return CELL_MUTED


def row_level(row: Observation) -> str:
    levels = [finding.level for finding in row.findings]
    return min(levels, key=lambda level: _LEVEL_ORDER[level]) if levels else LEVEL_OK


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _owner_text(report: ServerCheckReport, ip: str) -> str:
    if not ip:
        return ""
    owner = report.owner_of(ip)
    name = (owner.owner or (f"AS{owner.asn}" if owner.asn else "")) if owner is not None else ""
    return f"{ip} ({name})" if name else ip


def _row_tooltip(report: ServerCheckReport, row: Observation) -> str:
    lines = [f"{row.target.provider} — {row.target.address}"]
    for transport in TRANSPORTS:
        cell = row.cell(transport)
        text = cell_text(transport, cell)
        if cell.state in (STATE_FAIL, STATE_SKIP) and cell.reason:
            text = cell.reason
        elif cell.unstable:
            text = (
                f"{format_ms(cell.elapsed_ms)}, ответил на {cell.answered} из {cell.attempts} запросов "
                f"(остальные: {cell.reason})"
            )
        lines.append(f"{TRANSPORT_TITLES[transport]}: {text}")
    if row.udp_egress or row.secure_egress:
        lines.append(f"Кто выполняет обычные запросы: {_owner_text(report, row.udp_egress) or 'не узнали'}")
        lines.append(f"Кто выполняет шифрованные: {_owner_text(report, row.secure_egress) or 'не узнали'}")
    for finding in row.findings:
        lines.append(f"{_LEVEL_MARKS[finding.level]} {_sentence(finding.text)}")
    return "\n".join(lines)


def build_rows(report: ServerCheckReport) -> tuple[ServerRow, ...]:
    rows: list[ServerRow] = []
    for row in report.rows:
        shown = sorted(
            (finding for finding in row.findings if finding.code not in _SHOWN_IN_CELLS),
            key=lambda item: _LEVEL_ORDER[item.level],
        )
        if shown:
            note = " · ".join(_SHORT_NOTES.get(finding.code) or _sentence(finding.text) for finding in shown)
            note_level = shown[0].level
        else:
            note = "Без замечаний" if report.finished and not row.findings else ""
            note_level = LEVEL_OK
        rows.append(
            ServerRow(
                server=row.target.provider,
                address=row.target.address,
                cells=tuple(cell_text(transport, row.cell(transport)) for transport in TRANSPORTS),
                cell_levels=tuple(cell_level(transport, row.cell(transport)) for transport in TRANSPORTS),
                note=note,
                note_level=note_level,
                level=row_level(row),
                tooltip=_row_tooltip(report, row),
            )
        )
    return tuple(rows)


def _address_status(row: Observation, intercepted: bool) -> str:
    dns_cells = [row.cell(transport) for transport in TRANSPORTS if transport != TRANSPORT_ICMP]
    if not any(cell.state == STATE_OK for cell in dns_cells):
        return CARD_SILENT if any(cell.state == STATE_FAIL for cell in dns_cells) else CARD_OK
    codes = {finding.code for finding in row.findings}
    # Чужая сеть в ответах — вина дороги, только если перехват подтверждён общим итогом.
    if codes & _NETWORK_CODES or (intercepted and CODE_FOREIGN_ANSWERS in codes):
        return CARD_NETWORK
    if codes & _CLOSED_CODES or any(
        finding.code == CODE_UNSTABLE and finding.level == LEVEL_WARN for finding in row.findings
    ):
        return CARD_PARTIAL
    if CODE_SELF_FILTER in codes:
        return CARD_SELF
    return CARD_OK


def _best_time(rows: list[Observation]) -> str:
    for transport in (TRANSPORT_DOH, TRANSPORT_DOT, TRANSPORT_UDP):
        times = [
            row.cell(transport).elapsed_ms
            for row in rows
            if row.cell(transport).state == STATE_OK and row.cell(transport).elapsed_ms is not None
        ]
        if times:
            return f"{TRANSPORT_TITLES[transport].split()[0]} {format_ms(min(times))}"
    return ""


def build_cards(report: ServerCheckReport) -> tuple[ServerCard, ...]:
    """Карточки серверов: сначала те, кому доказанно мешает сеть, в конце — молчащие; внутри группы — по скорости."""
    intercepted = any(finding.code == CODE_INTERCEPTED for finding in report.findings)
    shown = dict(zip((id(row) for row in report.rows), build_rows(report)))
    by_server: dict[str, list[Observation]] = {}
    for row in report.rows:
        by_server.setdefault(row.target.provider, []).append(row)

    cards: list[tuple[int, float, ServerCard]] = []
    for server, rows in by_server.items():
        statuses = [_address_status(row, intercepted) for row in rows]
        alive = [status for status in statuses if status != CARD_SILENT]
        # Сервер жив, если отвечает хоть один его адрес; общий вывод — по худшему из живых.
        status = min(alive, key=CARD_ORDER.index) if alive else CARD_SILENT
        notes = list(
            dict.fromkeys(
                _CARD_NOTES[finding.code]
                for level in (LEVEL_FAIL, LEVEL_WARN, LEVEL_INFO)
                for row in rows
                for finding in row.findings
                if finding.level == level and finding.code in _CARD_NOTES and finding.code != CODE_DEAD
            )
        )
        silent = len(statuses) - len(alive)
        if alive and silent:
            notes.append(f"Молчит адресов: {silent} из {len(rows)}")
        best = _best_time(rows)
        # Для порядка внутри группы: чем быстрее шифрованный ответ, тем выше.
        speed = min(
            (
                row.cell(TRANSPORT_DOH).elapsed_ms or float("inf")
                for row in rows
                if row.cell(TRANSPORT_DOH).state == STATE_OK
            ),
            default=float("inf"),
        )
        addresses = tuple(shown[id(row)] for row in rows)
        card = ServerCard(
            server=server,
            status=status,
            note=" · ".join(notes),
            best=best,
            dots=tuple(
                tuple(cell_level(transport, row.cell(transport)) for row in rows)
                for transport in TRANSPORTS
                if transport != TRANSPORT_ICMP
            ),
            addresses=addresses,
            tooltip=f"{CARD_TITLES[status]}. {CARD_HINTS[status]}\n\n" + "\n\n".join(row.tooltip for row in addresses),
        )
        cards.append((CARD_ORDER.index(status), speed, card))
    cards.sort(key=lambda item: (item[0], item[1], item[2].server.lower()))
    return tuple(card for _order, _time, card in cards)


def count_cards(cards) -> dict[str, int]:
    counts = dict.fromkeys(CARD_ORDER, 0)
    for card in cards:
        counts[card.status] += 1
    return counts


def build_status(report: ServerCheckReport) -> InfoLine:
    done, total = len(report.rows), report.total
    if not report.finished:
        return InfoLine(f"Проверяем серверы: готово {done} из {total} адресов…", TONE_ACCENT)
    if report.stopped:
        return InfoLine(f"Проверка остановлена. Успели проверить {done} из {total} адресов.", TONE_WARNING)
    if not total:
        return InfoLine("В списке нет ни одного сервера для проверки.", TONE_WARNING)
    text = f"Проверено {done} из {total} адресов за {report.elapsed_s:.0f} с."
    if report.timed_out:
        return InfoLine(f"{text} Часть проверок не уложилась во время и была прервана.", TONE_WARNING)
    return InfoLine(text, TONE_MUTED)


def build_summary(report: ServerCheckReport) -> tuple[InfoLine, ...]:
    return tuple(InfoLine(finding.text, _LEVEL_TONES[finding.level]) for finding in report.findings)


def build_text_report(report: ServerCheckReport) -> str:
    """Полный текст для окна отчёта и для отправки: таблица, итог и подробности по адресам."""
    lines = ["ПРОВЕРКА DNS-СЕРВЕРОВ", build_status(report).text, ""]
    if report.findings:
        lines.append("Итог:")
        lines.extend(f"  {_LEVEL_MARKS[finding.level]} {finding.text}" for finding in report.findings)
        lines.append("")
    if report.bypass:
        lines.append(f"Работали во время проверки: {', '.join(report.bypass)}")
    if report.canary is not None:
        answered = "ОТВЕТИЛ — запросы перехватываются" if report.canary else "молчит, как и должен"
        lines.append(f"Контрольный адрес без DNS-сервера: {answered}")
        lines.append("")

    rows = build_rows(report)
    titles = [TRANSPORT_TITLES[transport] for transport in TRANSPORTS]
    server_width = max([len("Сервер"), *(len(row.server) for row in rows)])
    address_width = max([len("Адрес"), *(len(row.address) for row in rows)])
    widths = [max([len(title), *(len(row.cells[index]) for row in rows)]) for index, title in enumerate(titles)]
    header = f"{'Сервер':<{server_width}}  {'Адрес':<{address_width}}  " + "  ".join(
        f"{title:<{width}}" for title, width in zip(titles, widths)
    )
    lines.append(header.rstrip())
    for row in rows:
        cells = "  ".join(f"{text:<{width}}" for text, width in zip(row.cells, widths))
        lines.append(f"{row.server:<{server_width}}  {row.address:<{address_width}}  {cells}".rstrip())

    details = [row for row in report.rows if row.findings or row.udp_egress or row.secure_egress]
    if details:
        lines.extend(["", "Подробности:"])
    for row in details:
        lines.append(f"{row.target.provider} ({row.target.address})")
        if row.udp_egress or row.secure_egress:
            lines.append(f"  обычные запросы выполняет: {_owner_text(report, row.udp_egress) or 'не узнали'}")
            lines.append(f"  шифрованные выполняет: {_owner_text(report, row.secure_egress) or 'не узнали'}")
        for fact in row.domains:
            if fact.udp_status != fact.secure_status or fact.udp_ips != fact.secure_ips:
                lines.append(
                    f"  {fact.domain}: обычным путём {fact.udp_status} {', '.join(fact.udp_ips) or '—'}; "
                    f"шифрованным {fact.secure_status} {', '.join(fact.secure_ips) or '—'}"
                )
        for finding in row.findings:
            lines.append(f"  {_LEVEL_MARKS[finding.level]} {_sentence(finding.text)}")
    return "\n".join(lines)


__all__ = [
    "CELL_FAIL",
    "CELL_MUTED",
    "CELL_OK",
    "CELL_WARN",
    "CARD_GROUPS",
    "CARD_HINTS",
    "CARD_NETWORK",
    "CARD_OK",
    "CARD_ORDER",
    "CARD_SELF",
    "CARD_PARTIAL",
    "CARD_SILENT",
    "CARD_TITLES",
    "ServerCard",
    "build_cards",
    "count_cards",
    "TRANSPORT_TITLES",
    "ServerRow",
    "build_rows",
    "build_status",
    "build_summary",
    "build_text_report",
    "cell_level",
    "cell_text",
    "format_ms",
    "row_level",
]
