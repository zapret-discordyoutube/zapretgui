"""Чистые решения вкладки «Проверка домена»: без Qt и без сети.

Движок (`dns.domain_lookup`) отдаёт данные, а здесь они превращаются в то, что
видит человек: строки таблицы, подписи, статус и текстовый отчёт. Вкладка
только показывает готовое.
"""

from __future__ import annotations

from dataclasses import dataclass

from diagnostics import service_view
from diagnostics.path_trace import FILTER_FOUND, judge_filter
from diagnostics.quic_probe import QUIC_BLOCKED_BY_NAME, QUIC_FLAKY, QUIC_OK, QUIC_SILENT
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
    forged_answers,
    windows_blind,
    ROAD_BLOCKED,
    ROAD_DEAD,
    road_is_the_only_way,
    road_trouble,
)
from utils.dns_wire import STATUS_EMPTY, STATUS_NXDOMAIN, STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT

TONE_MUTED = "muted"
TONE_ACCENT = "accent"
TONE_SUCCESS = "success"
TONE_WARNING = "warning"
TONE_ERROR = "error"


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
        # Подпись — имя владельца сервера, если он известен; сам адрес показывается рядом.
        label = answer.server.label if answer.server.label != answer.server.address else ""
        return f"Системный DNS · {label}" if label else "Системный DNS"
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
        if answer.tcp_ipv4:
            tooltip_lines.append("По TCP тот же сервер ответил: " + ", ".join(answer.tcp_ipv4))
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


# Обычный DNS (UDP, порт 53) идёт открытым текстом, и ответ на него можно подделать по дороге;
# шифрованный запрос фильтр прочитать и подменить не может.
DNS_CURE = "Помогает DNS с шифрованием: страница «Настройка DNS»."


def own_dns_trouble(report: DomainLookupReport) -> str:
    """Главная беда с DNS на самом компьютере одной фразой. Пусто — её нет.

    Именно она отвечает на вопрос «проверка сайт открывает, а браузер нет»:
    проверка берёт адрес по шифрованному каналу, а браузер спрашивает Windows.
    """
    if windows_blind(report.answers):
        return "Windows адрес сайта не получает — браузер и программы его не найдут"
    if any(item.server.kind == SERVER_SYSTEM for item in forged_answers(report.answers)):
        return "DNS этого компьютера получает подставной ответ «такого домена нет»"
    return ""


def road_problem(report: DomainLookupReport) -> str:
    """Беда на дороге программ одной фразой. Пусто — её нет.

    Windows при живом IPv6 ставит первым адрес IPv6, и программы идут на него. Сайт при этом
    проверяется по IPv4 — и «открывается», хотя программы до него не доходят.
    """
    kind = road_trouble(report)
    if not kind:
        return ""
    what = "соединение по IPv6 не устанавливается" if kind == ROAD_DEAD else "по IPv6 сайт не открывается"
    if road_is_the_only_way(report):
        return f"Windows отдаёт программам только адреса IPv6, а {what}"
    return f"Windows первым отдаёт программам адрес IPv6, а {what}"


def road_is_fatal(report: DomainLookupReport) -> bool:
    """Программам некуда идти: Windows отдаёт одни адреса IPv6, и они не работают.

    Когда рядом есть адрес IPv4, браузер переходит на него сам — это помеха, а не поломка.
    """
    return bool(road_trouble(report)) and road_is_the_only_way(report)


def road_cure(report: DomainLookupReport) -> str:
    """Что сделать, когда дорога программ не работает."""
    road = report.road
    service = report.hosts_service
    ip = road.ip.lower() if road is not None else ""
    if ip and ip in report.hosts_managed:
        return (
            f"Этот адрес записала программа. Выключите и снова включите тумблер «{service}» на странице "
            "«Редактор hosts»: адреса перепроверятся, и останутся те, по которым сайт открывается."
        )
    if ip and ip in report.hosts_rows:
        if service and report.hosts_managed:
            return (
                f"Строка с этим адресом записана в файле hosts не программой и мешает сервису «{service}». Выключите и "
                "снова включите его тумблер на странице «Редактор hosts» — программа уберёт эту строку сама."
            )
        if service:
            return (
                f"Строка с этим адресом записана в файле hosts не программой. Включите тумблер «{service}» на странице "
                "«Редактор hosts»: программа запишет рабочие адреса и уберёт эту строку сама."
            )
        return (
            "Строка с этим адресом записана в файле hosts не программой. "
            "Удалите её сами: страница «Редактор hosts», раздел «Файл hosts»."
        )
    if service:
        return (
            f"Включите тумблер «{service}» на странице «Редактор hosts»: программа запишет сайту адреса IPv4, "
            "и программы пойдут по ним."
        )
    return "Помогает отключить IPv6 в свойствах сетевого адаптера Windows или починить его на роутере."


def hosts_advice(report: DomainLookupReport) -> str:
    """Совет включить тумблер «Редактора hosts», когда доказано, что по его адресам сайт открывается. Пусто — совета нет."""
    road = getattr(report, "hosts_road", None)
    site = getattr(report, "site", None) or {}
    if road is None or road.opens is not True or str(site.get("level") or "") != "fail":
        return ""
    return (
        f"По адресу из «Редактора hosts» ({road.ip}) этот сайт открывается: включите там тумблер "
        f"«{report.hosts_service}»."
    )


def hosts_try_note(report: DomainLookupReport) -> str:
    """Чем кончилась проба адреса из «Редактора hosts», когда совета она не дала. Пусто — пробы не было.

    Без этой строки непонятно, пробовала ли проверка тумблер вообще: «не советует» читалось
    как «не проверяет» (замечание владельца 2026-10-14).
    """
    road = getattr(report, "hosts_road", None)
    if road is None or hosts_advice(report):
        return ""
    if road.tcp.status != "ok":
        what = "соединение не установилось"
    elif road.opens is False:
        what = "приветствие с именем сайта тоже оборвали"
    else:
        what = "проба ничего не показала"
    return (
        f"Адрес из «Редактора hosts» ({road.ip}) тоже проверен — {what}. Значит, сейчас дело не в адресе: "
        f"тумблер «{report.hosts_service}» в этот раз не помог бы."
    )


def build_road_lines(report: DomainLookupReport) -> tuple[InfoLine, ...]:
    """Строки о дороге программ: вывод, что по ней получается и что делать."""
    road = getattr(report, "road", None)
    if road is None:
        return ()
    kind = road_trouble(report)
    lines: list[InfoLine] = []
    if kind:
        if kind == ROAD_DEAD:
            what = "соединение с ним не устанавливается (порт 443, две попытки; пинг в этом выводе не участвует)"
        else:
            what = "по нему сайт не открывается: соединение есть, но приветствие с именем сайта до сервера не доходит"
        if road_is_the_only_way(report):
            text = (
                f"Программам некуда идти: Windows отдаёт им только адреса IPv6 (первый — {road.ip}), а {what}. "
                "По IPv4 сервер отвечает, но Windows этот адрес программам не называет — сайт не откроется ни в "
                f"браузере, ни в программах. {road_cure(report)}"
            )
            lines.append(InfoLine(text, TONE_ERROR))
        else:
            text = (
                f"Программы сначала идут не туда: Windows первым отдаёт им адрес IPv6 {road.ip}, а {what}. "
                "Браузер обычно сам переходит на IPv4, а другие программы могут долго ждать или не открыть сайт. "
                f"{road_cure(report)}"
            )
            lines.append(InfoLine(text, TONE_WARNING))
    tcp = road.tcp
    if tcp.status == "ok":
        lines.append(InfoLine(f"Подключение по IPv6 к порту {tcp.port} ({road.ip}): успешно за {_ms(tcp.elapsed_ms)}.", TONE_SUCCESS))
    else:
        reason = {"timeout": "нет ответа", "refused": "порт закрыт"}.get(tcp.status, f"ошибка — {tcp.detail}")
        lines.append(InfoLine(f"Подключение по IPv6 к порту {tcp.port} ({road.ip}): {reason}.", TONE_WARNING))
    tones = {"ok": TONE_SUCCESS, "fail": TONE_WARNING}
    lines += [InfoLine(f"По IPv6 · {title}: {text}.", tones.get(state, TONE_MUTED)) for title, state, text in road.lines]
    return tuple(lines)


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
    forged = forged_answers(answers)
    if forged:
        names = ", ".join(_server_name(item) for item in forged[:4])
        if any(item.server.kind == SERVER_SYSTEM for item in forged):
            tail = f" Среди них DNS этого компьютера, поэтому браузер и программы сайт не находят. {DNS_CURE}"
        elif windows_blind(answers):
            tail = f" Windows адрес тоже не получила: браузер и программы сайт не найдут. {DNS_CURE}"
        else:
            tail = " DNS этого компьютера среди них нет."
        return InfoLine(
            f"{text} Вместо настоящего ответа пришёл подставной «такого домена нет» — его присылает фильтр по дороге, "
            f"а не сам сервер: {names}.{tail}",
            TONE_WARNING,
        )
    if windows_blind(answers):
        return InfoLine(
            f"{text} Windows адрес не получила, хотя серверы его знают: браузер и программы сайт не найдут. {DNS_CURE}",
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

_QUIC_TONES = {QUIC_OK: TONE_SUCCESS, QUIC_BLOCKED_BY_NAME: TONE_ERROR, QUIC_FLAKY: TONE_WARNING, QUIC_SILENT: TONE_MUTED}


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
    road = build_road_lines(report)
    # Вывод о дороге программ — первой строкой: он объясняет, почему «всё отвечает», а сайт не открывается.
    head = road[:1] if road_trouble(report) else ()
    return (*head, *lines, *road[len(head):])


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


@dataclass(frozen=True, slots=True)
class Row:
    """Строка для экрана: состояние (ok / warn / fail / info / unknown), подпись и значение."""

    state: str
    name: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class RowGroup:
    title: str
    rows: tuple[Row, ...]


_LEVEL_STATES = {LEVEL_OK: "ok", LEVEL_WARN: "warn", LEVEL_FAIL: "fail", LEVEL_UNKNOWN: "unknown"}


def build_path_rows(report: DomainLookupReport) -> tuple[Row, ...]:
    """Узлы дороги строками; перед узлом, за которым уже работает фильтр, стоит отметка."""
    route = report.route
    if route is None or not route.supported or not route.hops:
        return ()
    verdict = _filter_verdict(report)
    filter_hop = verdict.hop if verdict is not None and verdict.code == FILTER_FOUND else None
    rows: list[Row] = []
    for hop in route.hops:
        if hop.ttl == filter_hop:
            rows.append(Row("fail", FILTER_MARK))
        time_text = "" if hop.rtt_ms is None else ("< 1 мс" if hop.rtt_ms < 1 else f"{round(hop.rtt_ms)} мс")
        last = route.reached and hop is route.hops[-1]
        parts = [hop.address or "не ответил", time_text, "сам сервер" if last else ""]
        rows.append(Row("info", f"Узел {hop.ttl}", " · ".join(part for part in parts if part)))
    if filter_hop is not None and filter_hop > route.hops[-1].ttl:
        rows.append(Row("fail", FILTER_MARK))
    return tuple(rows)


def build_answer_groups(report: DomainLookupReport) -> tuple[RowGroup, ...]:
    """Ответы DNS-серверов одной группой: сервер, что ответил и за сколько."""
    rows = tuple(
        Row(
            _LEVEL_STATES.get(row.level, "info"),
            row.server if row.address in ("", "—") else f"{row.server} · {row.address}",
            " · ".join(part for part in (row.result, row.time) if part),
        )
        for row in build_answer_rows(report)
    )
    return (RowGroup("Что отвечают DNS-серверы", rows),) if rows else ()


# На экране каждая строка — отдельный виджет: сотни имён делали бы вкладку тяжёлой.
# Полный список остаётся в отчёте.
NEIGHBOR_ROWS_LIMIT = 40


def build_neighbor_groups(report: DomainLookupReport, limit: int = NEIGHBOR_ROWS_LIMIT) -> tuple[RowGroup, ...]:
    """«Кто ещё на этом адресе»: по группе на источник."""
    if not report.primary_ip:
        return ()
    groups: list[RowGroup] = []
    for source in report.sources:
        state = "info" if source.status == SOURCE_OK else "unknown"
        rows = [Row(state, _capital_first(_source_state(source)))]
        if source.extra:
            rows.append(Row("info", source.extra))
        rows += [Row("info", name) for name in source.names[:limit]]
        if len(source.names) > limit:
            rows.append(Row("unknown", f"… и ещё {len(source.names) - limit} — полный список в отчёте (кнопка «Отчёт»)"))
        groups.append(RowGroup(source_title(source.key), tuple(rows)))
    return tuple(groups)


def _capital_first(text: str) -> str:
    return text[:1].upper() + text[1:]


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
    # Строка итога называет главное: «адрес и число доменов» при закрытом сайте читалось как «всё хорошо».
    site = getattr(report, "site", None) or {}
    if str(site.get("level") or "") == "fail" and site.get("headline"):
        advice = hosts_advice(report)
        return InfoLine(f"Готово за {seconds}. {site['headline']}." + (f" {advice}" if advice else ""), TONE_ERROR)
    road = road_problem(report)
    if road:
        tone = TONE_ERROR if road_is_fatal(report) else TONE_WARNING
        return InfoLine(f"Готово за {seconds}. {road} (карточка «Пинг и сеть»).", tone)
    trouble = own_dns_trouble(report)
    if trouble:
        return InfoLine(f"Готово за {seconds}. {trouble} (карточка «Адреса с разных DNS-серверов»).", TONE_WARNING)
    return InfoLine(f"Готово за {seconds}: адрес {report.primary_ip}, доменов найдено: {count_neighbors(report)}.", TONE_SUCCESS)


def build_history_entry(report: DomainLookupReport) -> dict | None:
    """Запись для истории вкладки. None — проверку прервали или цель не разобрана."""
    from diagnostics import history

    if report.kind == KIND_INVALID or not report.finished or report.stopped:
        return None
    problems = [f"{row.server}: {row.result}" for row in build_answer_rows(report) if row.level == LEVEL_FAIL]
    verdict = _filter_verdict(report)
    if verdict is not None and verdict.code == FILTER_FOUND:
        problems.append(_capital_first(verdict.text))
    # Сайт не открывается или открывается с оговорками — это главное, что нашла проверка.
    site = getattr(report, "site", None) or {}
    site_level = str(site.get("level") or "")
    hard = bool(problems) or site_level == "fail"
    if site_level in ("fail", "warn") and site.get("headline"):
        problems.insert(0, str(site["headline"]))
    trouble = own_dns_trouble(report)
    if trouble:
        # Сайт сам доступен, а компьютер его не находит: в истории первой строкой нужна причина,
        # а не «открывается». Закрытый сайт остаётся первым — это важнее.
        problems.insert(1 if site_level == "fail" else 0, f"{report.target}: {trouble}")
    road = road_problem(report)
    if road:
        # Сайт сам доступен, а программы до него не доходят: в истории это беда, а не «открывается».
        problems.insert(1 if site_level == "fail" else 0, f"{report.target}: {road}. {road_cure(report)}")
        hard = hard or road_is_fatal(report)
    advice = hosts_advice(report)
    if advice:
        problems.append(advice)
    if hard:
        level = "fail"
    elif problems:
        level = "warn"
    elif not report.primary_ip or report.timed_out:
        level = "warn"
    elif site_level == "unknown":
        # Открывается ли сайт, проверка не узнала: это «не узнали», а не «всё хорошо».
        level = "unknown"
    else:
        level = "ok"
    return history.domain_entry(report.target, level, problems[0] if problems else build_status(report).text, problems)


def build_site_lines(report: DomainLookupReport) -> list[str]:
    """Раздел текстового отчёта «как открывается сайт»: те же пробы, что в BlockCheck."""
    site = getattr(report, "site", None)
    if not site:
        return []
    lines = ["", "=== Как открывается сайт ===", str(site.get("headline") or "")]
    # Те же разделы и слова, что на карточке сайта: их решает один модуль.
    lines += service_view.render_text(service_view.service_sections(site))
    advice = hosts_advice(report)
    if advice:
        lines.append(f"Что поможет: {advice}")
    note = hosts_try_note(report)
    if note:
        lines.append(note)
        road = report.hosts_road
        lines += [f"    {title}: {text}" for title, _state, text in road.lines]
    return [line for line in lines if line or line == ""]


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

    lines += build_site_lines(report)

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
    "TONE_ACCENT",
    "TONE_ERROR",
    "TONE_MUTED",
    "TONE_SUCCESS",
    "TONE_WARNING",
    "AnswerRow",
    "InfoLine",
    "build_answer_rows",
    "build_dns_summary",
    "Row",
    "RowGroup",
    "build_answer_groups",
    "build_neighbor_groups",
    "build_history_entry",
    "build_path_rows",
    "build_network_lines",
    "build_path_lines",
    "build_path_text",
    "build_ping_lines",
    "build_status",
    "build_text_report",
    "count_neighbors",
    "own_dns_trouble",
    "hosts_advice",
    "hosts_try_note",
    "road_cure",
    "road_is_fatal",
    "road_problem",
    "source_title",
]
