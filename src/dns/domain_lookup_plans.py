"""Чистые решения вкладки «Проверка домена»: без Qt и без сети.

Движок (`dns.domain_lookup`) отдаёт данные, а здесь они превращаются в то, что
видит человек: строки таблицы, подписи, статус и текстовый отчёт. Вкладка
только показывает готовое.
"""

from __future__ import annotations

from dataclasses import dataclass

from diagnostics.path_trace import FILTER_FOUND, judge_filter
from diagnostics.quic_probe import QUIC_BLOCKED_BY_NAME, QUIC_OK, QUIC_SILENT
from dns.domain_lookup import (
    KIND_DOMAIN,
    KIND_INVALID,
    LEVEL_FAIL,
    LEVEL_OK,
    LEVEL_UNKNOWN,
    LEVEL_WARN,
    SERVER_DOH,
    SERVER_SYSTEM,
    SERVER_WINDOWS,
    SOURCE_CERT_DEFAULT,
    SOURCE_CERT_NAMED,
    SOURCE_EMPTY,
    SOURCE_ERROR,
    SOURCE_HACKERTARGET,
    SOURCE_LIMIT,
    SOURCE_OK,
    SOURCE_PTR,
    SOURCE_SHODAN,
    SOURCE_SKIPPED,
    SOURCE_THC,
    DomainLookupReport,
    NeighborSource,
    PingReport,
    ResolverAnswer,
)
from utils.dns_wire import STATUS_EMPTY, STATUS_NXDOMAIN, STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT

TONE_MUTED = "muted"
TONE_ACCENT = "accent"
TONE_SUCCESS = "success"
TONE_WARNING = "warning"
TONE_ERROR = "error"

NEIGHBORS_SHOWN_LIMIT = 300

_LEVEL_TONES = {LEVEL_OK: TONE_SUCCESS, LEVEL_WARN: TONE_WARNING, LEVEL_FAIL: TONE_ERROR, LEVEL_UNKNOWN: TONE_MUTED}

_SOURCE_TITLES = {
    SOURCE_PTR: "Обратное имя адреса (PTR)",
    SOURCE_CERT_NAMED: "Сертификат сайта — на какие ещё домены он выдан",
    SOURCE_CERT_DEFAULT: "Сертификат адреса без имени сайта — кто здесь «хозяин»",
    SOURCE_THC: "Домены на этом адресе по базе THC (ip.thc.org)",
    SOURCE_HACKERTARGET: "Домены на этом адресе по базе HackerTarget (запасной)",
    SOURCE_SHODAN: "Имена и открытые порты по базе Shodan",
}

_PING_ERRORS = {
    "TIMEOUT": "не отвечает на пинг",
    "UNSUPPORTED": "пинг доступен только в Windows",
    "ICMP_OPEN_FAILED": "Windows не дала отправить пинг",
    "ICMP_1231": "нет связи по IPv6 (у вашего подключения нет IPv6)",
    "ICMP_11002": "сеть назначения недоступна",
    "ICMP_11003": "адрес назначения недоступен",
    "ICMP_11013": "пакет не дошёл: истёк счётчик пересылок (TTL)",
}


@dataclass(frozen=True, slots=True)
class AnswerRow:
    server: str
    address: str
    result: str
    time: str
    level: str
    tooltip: str


@dataclass(frozen=True, slots=True)
class InfoLine:
    text: str
    tone: str = TONE_MUTED


def level_tone(level: str) -> str:
    return _LEVEL_TONES.get(level, TONE_MUTED)


def source_title(key: str) -> str:
    return _SOURCE_TITLES.get(key, key)


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 1:
        return "<1 мс"
    return f"{value:.0f} мс"


def _server_name(answer: ResolverAnswer) -> str:
    kind = answer.server.kind
    if kind == SERVER_WINDOWS:
        return "Windows (как видят программы)"
    if kind == SERVER_SYSTEM:
        return f"Системный DNS · {answer.server.label}" if answer.server.label else "Системный DNS"
    return answer.server.label


def _answer_text(answer: ResolverAnswer) -> str:
    if answer.status == STATUS_OK:
        text = ", ".join(answer.addresses)
    elif answer.status == STATUS_NXDOMAIN:
        text = "такого домена нет"
    elif answer.status == STATUS_EMPTY:
        text = "адресов нет"
    elif answer.status == STATUS_TIMEOUT:
        text = "сервер не ответил"
    elif answer.status == STATUS_REFUSED:
        text = "сервер отказался отвечать"
    elif answer.server.kind == SERVER_WINDOWS:
        text = "не удалось узнать адрес"
    else:
        text = "ошибка запроса"
    if answer.note:
        text = f"{text} — {answer.note}"
    return text


def build_answer_rows(report: DomainLookupReport) -> tuple[AnswerRow, ...]:
    rows: list[AnswerRow] = []
    for answer in report.answers:
        tooltip_lines = [_server_name(answer)]
        if answer.server.address:
            protocol = "шифрованный запрос (DoH)" if answer.server.kind == SERVER_DOH else "обычный запрос (UDP, порт 53)"
            tooltip_lines.append(f"Сервер {answer.server.address}, {protocol}")
        if answer.ipv4:
            tooltip_lines.append("IPv4: " + ", ".join(answer.ipv4))
        if answer.ipv6:
            tooltip_lines.append("IPv6: " + ", ".join(answer.ipv6))
        if answer.cnames:
            tooltip_lines.append("Псевдонимы (CNAME): " + " → ".join(answer.cnames))
        if answer.ttl is not None:
            tooltip_lines.append(f"Ответ считается свежим ещё {answer.ttl} с (TTL)")
        if answer.note:
            tooltip_lines.append(answer.note)
        rows.append(
            AnswerRow(
                server=_server_name(answer),
                address=answer.server.address or "—",
                result=_answer_text(answer),
                time=_ms(answer.elapsed_ms),
                level=answer.level,
                tooltip="\n".join(tooltip_lines),
            )
        )
    return tuple(rows)


def build_dns_summary(report: DomainLookupReport) -> InfoLine:
    """Одна фраза над таблицей: сколько серверов ответило и есть ли что-то подозрительное."""
    answers = report.answers
    if not answers:
        return InfoLine("Спрашиваем DNS-серверы…")
    resolved = [item for item in answers if item.status == STATUS_OK]
    stubs = [item for item in answers if item.level == LEVEL_FAIL]
    odd = [item for item in answers if item.level == LEVEL_WARN]
    text = f"Адрес назвали {len(resolved)} из {len(answers)} серверов."
    if stubs:
        names = ", ".join(_server_name(item) for item in stubs[:4])
        return InfoLine(f"{text} Заглушку вместо настоящего адреса вернули: {names}.", TONE_ERROR)
    if report.intercepted:
        return InfoLine(
            f"{text} Внимание: обычные DNS-запросы перехватываются по пути (ответил даже несуществующий сервер), "
            "поэтому «разные» серверы — на деле один. Верьте строкам «шифрованный».",
            TONE_WARNING,
        )
    if odd:
        names = ", ".join(_server_name(item) for item in odd[:4])
        return InfoLine(f"{text} Отвечают не как остальные: {names}.", TONE_WARNING)
    if not resolved:
        return InfoLine(f"{text} Похоже, такого домена не существует.", TONE_WARNING)
    return InfoLine(f"{text} Разные адреса у разных серверов — это нормально для крупных сайтов.", TONE_SUCCESS)


def _ping_line(ping: PingReport, title: str) -> InfoLine:
    if not ping.supported:
        reason = _PING_ERRORS.get(ping.error_code, "пинг недоступен")
        return InfoLine(f"{title} {ping.ip}: {reason}.", TONE_MUTED)
    if ping.received <= 0:
        reason = _PING_ERRORS.get(ping.error_code, f"нет ответа ({ping.error_code})" if ping.error_code else "нет ответа")
        return InfoLine(f"{title} {ping.ip}: {reason}. Отправлено {ping.sent}, получено 0.", TONE_WARNING)
    tone = TONE_SUCCESS if ping.received == ping.sent else TONE_WARNING
    ttl = f", TTL {ping.ttl}" if ping.ttl else ""
    return InfoLine(
        f"{title} {ping.ip}: отправлено {ping.sent}, получено {ping.received}, потеряно {ping.lost_percent}%. "
        f"Время: мин {_ms(ping.min_ms)}, средн {_ms(ping.avg_ms)}, макс {_ms(ping.max_ms)}{ttl}.",
        tone,
    )


# ---------------------------------------------------------------------------
# Путь до сервера
# ---------------------------------------------------------------------------

FILTER_MARK = "── здесь стоит фильтр ──"

_QUIC_TONES = {QUIC_OK: TONE_SUCCESS, QUIC_BLOCKED_BY_NAME: TONE_ERROR, QUIC_SILENT: TONE_MUTED}


def _hops_word(count: int) -> str:
    """«1 узел», «2 узла», «5 узлов»."""
    if count % 10 == 1 and count % 100 != 11:
        return "узел"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "узла"
    return "узлов"


def _filter_verdict(report: DomainLookupReport):
    if report.filter_facts is None:
        return None
    return judge_filter(report.filter_facts, report.route)


def build_path_lines(report: DomainLookupReport) -> tuple[InfoLine, ...]:
    """Коротко: сколько узлов до сервера, что с QUIC и где стоит фильтр."""
    lines: list[InfoLine] = []
    route = report.route
    if route is not None and route.supported:
        answered = sum(1 for hop in route.hops if hop.address)
        if route.reached:
            count = len(route.hops)
            lines.append(InfoLine(f"До сервера {count} {_hops_word(count)}, ответили {answered}.", TONE_MUTED))
        elif route.hops:
            lines.append(
                InfoLine(
                    f"Пинг до сервера не дошёл: последний ответивший узел — {route.hops[-1].ttl}-й "
                    f"({route.hops[-1].address}). Многие серверы на пинг не отвечают, это ещё не блокировка.",
                    TONE_MUTED,
                )
            )
        else:
            lines.append(InfoLine("Ни один узел по дороге не ответил на пинг.", TONE_WARNING))
    if report.quic is not None:
        lines.append(InfoLine(f"QUIC (UDP 443): {report.quic.text}.", _QUIC_TONES.get(report.quic.code, TONE_MUTED)))
    verdict = _filter_verdict(report)
    if verdict is not None:
        tone = TONE_ERROR if verdict.code == FILTER_FOUND else TONE_MUTED
        lines.append(InfoLine(f"{verdict.text[:1].upper()}{verdict.text[1:]}.", tone))
    return tuple(lines)


def build_path_text(report: DomainLookupReport) -> str:
    """Таблица узлов; перед узлом, за которым уже работает фильтр, стоит отметка."""
    route = report.route
    if route is None or not route.supported or not route.hops:
        return ""
    verdict = _filter_verdict(report)
    filter_hop = verdict.hop if verdict is not None and verdict.code == FILTER_FOUND else None
    width = max(len(hop.address) for hop in route.hops) or 1
    rows: list[str] = []
    for hop in route.hops:
        if hop.ttl == filter_hop:
            rows.append(f"    {FILTER_MARK}")
        address = hop.address or "не ответил"
        time_text = "" if hop.rtt_ms is None else ("< 1 мс" if hop.rtt_ms < 1 else f"{round(hop.rtt_ms)} мс")
        rows.append(f"{hop.ttl:>2}  {address:<{max(width, len('не ответил'))}}  {time_text}".rstrip())
    if filter_hop is not None and filter_hop > route.hops[-1].ttl:
        rows.append(f"    {FILTER_MARK}")
    return "\n".join(rows)


def build_ping_lines(report: DomainLookupReport) -> tuple[InfoLine, ...]:
    if not report.primary_ip:
        if report.kind == KIND_DOMAIN and report.finished:
            return (InfoLine("Пинговать нечего: ни один DNS-сервер не назвал адрес.", TONE_WARNING),)
        return ()
    lines: list[InfoLine] = []
    lines.append(_ping_line(report.ping, "Пинг") if report.ping else InfoLine(f"Пинг {report.primary_ip}: проверяем…"))
    if report.ping6 is not None:
        lines.append(_ping_line(report.ping6, "Пинг по IPv6"))
    tcp = report.tcp
    if tcp is None:
        lines.append(InfoLine(f"Подключение к порту 443 ({report.primary_ip}): проверяем…"))
    elif tcp.status == "ok":
        lines.append(InfoLine(f"Подключение к порту {tcp.port} (HTTPS): успешно за {_ms(tcp.elapsed_ms)}.", TONE_SUCCESS))
    elif tcp.status == "refused":
        lines.append(InfoLine(f"Подключение к порту {tcp.port} (HTTPS): порт закрыт — сайта на этом адресе нет.", TONE_WARNING))
    elif tcp.status == "timeout":
        lines.append(InfoLine(f"Подключение к порту {tcp.port} (HTTPS): нет ответа.", TONE_WARNING))
    else:
        lines.append(InfoLine(f"Подключение к порту {tcp.port} (HTTPS): ошибка — {tcp.detail}.", TONE_WARNING))

    ping = report.ping
    if ping is not None and tcp is not None and ping.supported and ping.received == 0 and tcp.status == "ok":
        lines.append(InfoLine("Сервер просто не отвечает на пинг, но сам работает — это обычное дело.", TONE_MUTED))
    return tuple(lines)


def build_network_lines(report: DomainLookupReport) -> tuple[InfoLine, ...]:
    if not report.primary_ip:
        return ()
    network = report.network
    if network is None:
        return ()
    lines: list[InfoLine] = []
    owners = [f"AS{asn} {holder}".strip() for asn, holder in network.origins]
    if len(owners) > 1:
        lines.append(InfoLine("Владельцы сети (адрес объявляют несколько сетей): " + "; ".join(owners), TONE_ACCENT))
    elif network.owner or owners:
        lines.append(InfoLine(f"Владелец сети: {network.owner or owners[0]}", TONE_ACCENT))
    details: list[str] = []
    if network.asn:
        details.append(f"ASN: AS{network.asn}")
    if network.prefix:
        details.append(f"подсеть: {network.prefix}")
    if network.country:
        details.append(f"страна: {network.country}")
    if details:
        lines.append(InfoLine(", ".join(details)))
    return tuple(lines)


def _source_state(source: NeighborSource) -> str:
    if source.status == SOURCE_OK:
        if source.total is not None and source.total > len(source.names):
            return f"показано {len(source.names)} из {source.total}"
        if not source.names:
            return "имён не найдено"
        return f"найдено: {len(source.names)}"
    if source.status == SOURCE_EMPTY:
        return "ничего не найдено"
    if source.status == SOURCE_LIMIT:
        return f"лимит: {source.detail}" if source.detail else "лимит запросов исчерпан"
    if source.status == SOURCE_SKIPPED:
        return f"пропущено: {source.detail}" if source.detail else "пропущено"
    if source.status == SOURCE_ERROR:
        return f"не получилось: {source.detail}" if source.detail else "не получилось"
    return source.status


def build_neighbors_text(report: DomainLookupReport) -> str:
    """Текст блока «Кто ещё на этом адресе»: по разделу на источник."""
    if not report.primary_ip:
        return ""
    blocks: list[str] = []
    for source in report.sources:
        header = f"{source_title(source.key)} — {_source_state(source)}"
        names = [source.extra] if source.extra else []
        names += list(source.names[:NEIGHBORS_SHOWN_LIMIT])
        if len(source.names) > NEIGHBORS_SHOWN_LIMIT:
            names.append(f"… и ещё {len(source.names) - NEIGHBORS_SHOWN_LIMIT} (полный список — в отчёте)")
        blocks.append("\n".join([header, *(f"    {name}" for name in names)]))
    return "\n\n".join(blocks)


def count_neighbors(report: DomainLookupReport) -> int:
    names: set[str] = set()
    for source in report.sources:
        names.update(source.names)
    return len(names)


def build_status(report: DomainLookupReport) -> InfoLine:
    if report.kind == KIND_INVALID:
        return InfoLine(report.error, TONE_ERROR)
    if not report.finished:
        return InfoLine(f"Проверяем {report.target}…", TONE_ACCENT)
    seconds = f"{report.elapsed_s:.1f} с"
    if report.stopped:
        return InfoLine(f"Остановлено. Показано то, что успели узнать ({seconds}).", TONE_WARNING)
    if report.timed_out:
        return InfoLine(f"Часть проверок не успела закончиться за отведённое время ({seconds}).", TONE_WARNING)
    if not report.primary_ip:
        return InfoLine(f"Готово за {seconds}: адрес узнать не удалось.", TONE_WARNING)
    return InfoLine(f"Готово за {seconds}: адрес {report.primary_ip}, доменов найдено: {count_neighbors(report)}.", TONE_SUCCESS)


def build_text_report(report: DomainLookupReport) -> str:
    """Полный текстовый отчёт: для окна «Отчёт», копирования и поддержки."""
    lines: list[str] = [f"Проверка: {report.target}"]
    if report.kind == KIND_INVALID:
        lines.append(report.error)
        return "\n".join(lines)
    lines.append(build_status(report).text)

    if report.kind == KIND_DOMAIN:
        lines += ["", "=== Адреса с разных DNS ===", build_dns_summary(report).text]
        # Строки идут в том же порядке, что и ответы: одна строка на сервер.
        for row, answer in zip(build_answer_rows(report), report.answers):
            ttl = f", TTL {answer.ttl} с" if answer.ttl is not None else ""
            lines.append(f"{row.server} [{row.address}] ({row.time}{ttl}): {row.result}")
            if answer.cnames:
                lines.append("    CNAME: " + " → ".join(answer.cnames))

    ping_lines = build_ping_lines(report)
    if ping_lines:
        lines += ["", "=== Пинг ===", *(line.text for line in ping_lines)]
    path_lines = build_path_lines(report)
    if path_lines:
        lines += ["", "=== Путь до сервера ===", *(line.text for line in path_lines)]
        path_text = build_path_text(report)
        if path_text:
            lines += [path_text]

    network_lines = build_network_lines(report)
    if network_lines:
        lines += ["", f"=== Сеть адреса {report.primary_ip} ===", *(line.text for line in network_lines)]

    if report.sources:
        lines += ["", f"=== Кто ещё на адресе {report.primary_ip} ==="]
        for source in report.sources:
            lines.append(f"{source_title(source.key)} — {_source_state(source)}")
            if source.extra:
                lines.append(f"    {source.extra}")
            lines.extend(f"    {name}" for name in source.names)
    return "\n".join(lines)


__all__ = [
    "FILTER_MARK",
    "NEIGHBORS_SHOWN_LIMIT",
    "TONE_ACCENT",
    "TONE_ERROR",
    "TONE_MUTED",
    "TONE_SUCCESS",
    "TONE_WARNING",
    "AnswerRow",
    "InfoLine",
    "build_answer_rows",
    "build_dns_summary",
    "build_neighbors_text",
    "build_network_lines",
    "build_path_lines",
    "build_path_text",
    "build_ping_lines",
    "build_status",
    "build_text_report",
    "count_neighbors",
    "level_tone",
    "source_title",
]
