"""Как показывать отчёт проверки DNS-серверов: строки таблицы, итог и текст.

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
    CODE_DOH_BLOCKED,
    CODE_DOT_BLOCKED,
    CODE_TCP_BLOCKED,
    CODE_UDP_BLOCKED,
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
_SHOWN_IN_CELLS = frozenset({CODE_UDP_BLOCKED, CODE_TCP_BLOCKED, CODE_DOT_BLOCKED, CODE_DOH_BLOCKED})


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


def format_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return "< 1 мс" if value < 1 else f"{round(value)} мс"


def cell_text(transport: str, cell: Cell) -> str:
    if cell.state == STATE_OK:
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
        return CELL_OK
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
        if cell.state == STATE_FAIL and cell.reason:
            text = cell.reason
        elif cell.state == STATE_SKIP and cell.reason:
            text = cell.reason
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
            note = _sentence(shown[0].text)
            if len(shown) > 1:
                note = f"{note} (и ещё {len(shown) - 1})"
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
