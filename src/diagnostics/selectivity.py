"""Избирательность блокировки: имя × адрес × протокол, с контролями.

Блокировка редко бывает «всё или ничего». Фильтр на пути может читать имя сайта
(поле SNI в TLS, то же имя в первом пакете QUIC), смотреть на адрес узла или на
протокол и пропускать остальное. Вывод по одному адресу при этом остаётся
догадкой: у сайта открывается главная страница, а видеоузел режут; QUIC к одному
имени теряется, к другому на том же сервере проходит.

Здесь одна таблица замеров. Строки — имена (имена сайтов сервиса и три контроля:
постороннее имя, приветствие без имени и первое имя сервиса другими буквами),
столбцы — адреса сервисов, ячейки — исход приветствия TLS и первого пакета QUIC.
Контроль важен: если с посторонним именем или без имени тот же адрес отвечает,
адрес жив и режут именно имя; если не отвечает ничего, закрыт адрес или протокол.
Имя другими буквами (``googlevideo.COM``) сервер принимает как то же самое: если оно
проходит, а обычное нет, их различает фильтр, который ищет имя буква в букву.

Одна проба ничего не доказывает: узлы отвечают «через раз», и одиночная потеря
пакета выглядела в таблице как обрыв по имени (отчёт с МТС 15.11.2026: QUIC с
посторонним именем пропал на том же адресе, где «резали» имя сайта). Поэтому каждое
сочетание без ответа проверяется ещё раз: ответило со второго раза — «через раз»,
и в выводы такая ячейка не идёт. Адрес считается живым, только если на нём отвечает
постороннее имя: если пропадает и оно, винить имя сайта нечем.

Сбор (``collect``) отделён от вывода (``judge``): вывод — чистая функция над таблицей.
Пробы отправляются только по сигналу беды (``needs_matrix``): браузер их не шлёт.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from diagnostics.block_cause import case_variant

__all__ = [
    "ANSWERED",
    "CONTROL_NAME",
    "CUT",
    "FLAKY",
    "MAX_ADDRESSES",
    "MAX_NAMES",
    "NO_CONNECT",
    "SCOPE_ADDRESS",
    "SCOPE_FLAKY",
    "SCOPE_NAME",
    "SCOPE_NODE",
    "SCOPE_NONE",
    "QUIC_TIMEOUT_S",
    "TLS_TIMEOUT_S",
    "UNKNOWN",
    "Cell",
    "Finding",
    "MatrixFacts",
    "MatrixVerdict",
    "collect",
    "judge",
    "needs_matrix",
]

# Постороннее имя: существует, ни в одном списке блокировок его нет.
CONTROL_NAME = "example.com"
# Меньше проб, чем сочетаний: таблица не должна превращаться в нагрузку на сеть.
MAX_NAMES = 5
MAX_ADDRESSES = 6
# Сроки одной ячейки: короче обычных проб, потому что ячеек много и они идут одновременно.
TLS_TIMEOUT_S = 2.5
QUIC_TIMEOUT_S = 1.0

ANSWERED = "answered"
CUT = "cut"
# Без ответа в первый раз, с ответом на повторе: потеря случайная, имя тут не доказано.
FLAKY = "flaky"
# Соединение TCP не открылось вовсе: до приветствия с именем дело не дошло.
NO_CONNECT = "no_connect"
UNKNOWN = "unknown"
# Состояния, которые стоит проверить ещё раз: одиночная потеря бывает и без фильтра.
_RECHECKED = (CUT, NO_CONNECT)

PROTOCOL_TLS = "tls"
PROTOCOL_QUIC = "quic"
PROTOCOL_TITLES = {PROTOCOL_TLS: "TLS поверх TCP 443", PROTOCOL_QUIC: "QUIC поверх UDP 443"}

SCOPE_NAME = "name"
SCOPE_ADDRESS = "address"
SCOPE_NODE = "node"
SCOPE_FLAKY = "flaky"
SCOPE_NONE = "none"


@dataclass(frozen=True, slots=True)
class Cell:
    """Исход одного сочетания «имя на адресе». Время — в миллисекундах, None — ответа не было."""

    tls: str = UNKNOWN
    quic: str = UNKNOWN
    tls_ms: float | None = None
    quic_ms: float | None = None

    def state(self, protocol: str) -> str:
        return self.tls if protocol == PROTOCOL_TLS else self.quic

    def needs_recheck(self) -> bool:
        return self.tls in _RECHECKED or self.quic in _RECHECKED

    def merged(self, again: "Cell") -> "Cell":
        """Итог двух проб одного сочетания: ответ хотя бы на повторе — «через раз», иначе первая проба."""
        tls, tls_ms = (FLAKY, again.tls_ms) if self.tls in _RECHECKED and again.tls == ANSWERED else (self.tls, self.tls_ms)
        quic, quic_ms = (
            (FLAKY, again.quic_ms) if self.quic in _RECHECKED and again.quic == ANSWERED else (self.quic, self.quic_ms)
        )
        return Cell(tls, quic, tls_ms, quic_ms)


@dataclass(frozen=True, slots=True)
class MatrixFacts:
    # Имена сайтов сервиса (без контролей) и адреса, на которых они проверялись: (подпись, IP).
    names: tuple[str, ...]
    addresses: tuple[tuple[str, str], ...]
    # Ключ ячейки — (имя, IP); имя "" — приветствие без имени, ``CONTROL_NAME`` — посторонее имя.
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    cancelled: bool = False
    # Первое имя сервиса другими буквами (``block_cause.case_variant``). Пусто — такой строки нет.
    recased: str = ""

    @property
    def controls(self) -> tuple[str, ...]:
        """Строки-контроли: по ним видно, жив ли адрес сам по себе."""
        return (CONTROL_NAME, "", *((self.recased,) if self.recased else ()))


@dataclass(frozen=True, slots=True)
class Finding:
    protocol: str
    scope: str
    level: str
    title: str
    text: str
    blocked: tuple[str, ...] = ()
    passing: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "scope": self.scope,
            "level": self.level,
            "title": self.title,
            "text": self.text,
            "blocked": list(self.blocked),
            "passing": list(self.passing),
        }


@dataclass(frozen=True, slots=True)
class MatrixVerdict:
    findings: tuple[Finding, ...]
    # Таблица для показа: строки «имя — исход по каждому адресу».
    table: tuple[dict, ...]
    addresses: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict:
        return {
            "findings": [item.as_dict() for item in self.findings],
            "table": list(self.table),
            "addresses": [{"host": host, "ip": ip} for host, ip in self.addresses],
        }


def needs_matrix(quic_codes: Iterable[str], kinds: Iterable[str], unstable: Iterable[str] = ()) -> bool:
    """Таблица нужна только при сигнале беды: потеря QUIC по имени, блокировка или нестабильность."""
    return any(code == "blocked_by_name" for code in quic_codes) or any(kinds) or any(unstable)


def collect(
    names: Sequence[str],
    addresses: Sequence[tuple[str, str]],
    probe: Callable[[str, str], Cell],
    *,
    submit: Callable,
    should_stop: Callable[[], bool] = lambda: False,
) -> MatrixFacts:
    """Замеряет все сочетания «имя на адресе» вместе с контролями. ``probe(имя, ip)`` — одна ячейка."""
    names = tuple(dict.fromkeys(name for name in names if name))[:MAX_NAMES]
    addresses = tuple(dict.fromkeys(addresses))[:MAX_ADDRESSES]
    recased = case_variant(names[0]) if names else ""
    rows = (*names, CONTROL_NAME, "", *((recased,) if recased else ()))
    keys = [(name, ip) for name in rows for _host, ip in addresses]
    futures = {}
    for name, ip in keys:
        if should_stop():
            return MatrixFacts(names, addresses, {}, cancelled=True, recased=recased)
        futures[(name, ip)] = submit(probe, name, ip)
    cells = {key: future.result() for key, future in futures.items()}
    # Второй заход — только по сочетаниям без ответа: одна потеря пакета ещё не блокировка.
    again = {}
    for key, cell in cells.items():
        if cell.needs_recheck() and not should_stop():
            again[key] = submit(probe, *key)
    for key, future in again.items():
        cells[key] = cells[key].merged(future.result())
    return MatrixFacts(names, addresses, cells, cancelled=should_stop(), recased=recased)


def _row(facts: MatrixFacts, name: str, protocol: str) -> dict[str, str]:
    return {ip: facts.cells.get((name, ip), Cell()).state(protocol) for _host, ip in facts.addresses}


def _label(facts: MatrixFacts, ip: str) -> str:
    hosts = [host for host, address in facts.addresses if address == ip]
    return f"{hosts[0]} ({ip})" if hosts else ip


def _names_word(names: Sequence[str]) -> str:
    return ", ".join(names)


def _row_title(facts: MatrixFacts, name: str) -> str:
    if not name:
        return "без имени"
    if name == facts.recased:
        return f"{name} (другими буквами)"
    return name


def _alive(facts: MatrixFacts, rows: dict[str, dict[str, str]], ip: str) -> bool:
    """Адрес жив сам по себе: отвечает постороннее имя (а если его исход неизвестен — любой другой контроль).

    Если постороннее имя на этом адресе тоже пропадает, теряется всё подряд, и винить имя сайта нечем.
    """
    neutral = rows[CONTROL_NAME].get(ip)
    if neutral == ANSWERED:
        return True
    if neutral != UNKNOWN:
        return False
    return any(rows[name].get(ip) == ANSWERED for name in facts.controls)


def _flaky_finding(facts: MatrixFacts, rows: dict[str, dict[str, str]], protocol: str) -> Finding | None:
    title = PROTOCOL_TITLES[protocol]
    spots = [
        f"{_row_title(facts, name)} — на {', '.join(_label(facts, ip) for ip in ips)}"
        for name, row in rows.items()
        if (ips := [ip for _host, ip in facts.addresses if row.get(ip) == FLAKY])
    ]
    if not spots:
        return None
    shown = "; ".join(spots[:6]) + (f" и ещё {len(spots) - 6}" if len(spots) > 6 else "")
    text = (
        f"{title}: часть сочетаний ответила только со второй попытки — {shown}. Пакеты здесь теряются через раз, "
        "поэтому эти ячейки в выводы не идут: одиночная потеря — ещё не блокировка по имени."
    )
    return Finding(protocol, SCOPE_FLAKY, "info", f"{title}: ответ через раз", text)


def _judge_protocol(facts: MatrixFacts, protocol: str) -> list[Finding]:
    title = PROTOCOL_TITLES[protocol]
    rows = {name: _row(facts, name, protocol) for name in (*facts.names, *facts.controls)}
    flaky = _flaky_finding(facts, rows, protocol)
    tail = [flaky] if flaky is not None else []
    if not any(state == ANSWERED for row in rows.values() for state in row.values()):
        # Не ответило ничего: протокол к этим серверам не работает совсем (или не поддержан) — имя тут ни при чём.
        if all(state == CUT for row in rows.values() for state in row.values()):
            text = (
                f"{title}: ни одно сочетание имени и адреса не ответило, даже с посторонним именем и без имени. "
                "Это не блокировка по имени: протокол не доходит до серверов вовсе (закрыт порт, "
                "серверы его не поддерживают или режут весь протокол). Избирательность не определить."
            )
            return [Finding(protocol, SCOPE_ADDRESS, "info", f"{title}: не отвечает совсем", text)]
        return tail
    findings: list[Finding] = []
    by_name: dict[str, list[str]] = {}
    control_ok_ips = {ip for _host, ip in facts.addresses if _alive(facts, rows, ip)}
    for name in facts.names:
        for _host, ip in facts.addresses:
            if rows[name].get(ip) == CUT and ip in control_ok_ips:
                by_name.setdefault(name, []).append(ip)
    if by_name:
        blocked = tuple(by_name)
        passing = tuple(
            name
            for name in facts.names
            if name not in by_name and any(state == ANSWERED for state in rows[name].values())
        )
        spots = "; ".join(f"{name} — на {', '.join(_label(facts, ip) for ip in ips)}" for name, ips in by_name.items())
        text = (
            f"{title}: соединение с именем {_names_word(blocked)} обрывается, а с посторонним именем ({CONTROL_NAME}) "
            "или без имени тот же адрес отвечает. Адрес жив, режут именно имя сайта: так работает фильтр на пути "
            f"(оборудование оператора связи читает имя в начале соединения). Где именно: {spots}."
        )
        if passing:
            text += (
                f" Имена {_names_word(passing)} проходят на тех же адресах, поэтому блокировка избирательна: "
                "она задевает только часть имён сервиса."
            )
        findings.append(Finding(protocol, SCOPE_NAME, "warn", f"{title}: режут по имени", text, blocked, passing))
    if facts.recased and facts.names:
        first = facts.names[0]
        letters = [
            ip
            for _host, ip in facts.addresses
            if rows[first].get(ip) == CUT and rows[facts.recased].get(ip) == ANSWERED and ip in control_ok_ips
        ]
        if letters:
            text = (
                f"{title}: с именем {first} соединение обрывается, а то же имя другими буквами ({facts.recased}) "
                f"на тех же адресах проходит — на {', '.join(_label(facts, ip) for ip in letters)}. Сервер регистр букв "
                "не различает, значит, различает фильтр на пути: он ищет имя буква в букву. Это доказывает, что режут "
                "именно имя, и такую блокировку стратегия Zapret обходит."
            )
            findings.append(Finding(protocol, SCOPE_NAME, "warn", f"{title}: фильтр ищет имя буква в букву", text, (first,)))
    node: list[str] = []
    for name in facts.names:
        cut_ips = [ip for _host, ip in facts.addresses if rows[name].get(ip) == CUT and ip in control_ok_ips]
        ok_ips = [ip for _host, ip in facts.addresses if rows[name].get(ip) == ANSWERED]
        if cut_ips and ok_ips:
            node.append(
                f"{name}: обрывается на {', '.join(_label(facts, ip) for ip in cut_ips)}, "
                f"проходит на {', '.join(_label(facts, ip) for ip in ok_ips)}"
            )
    if node:
        text = (
            f"{title}: одно и то же имя на разных адресах ведёт себя по-разному — {'; '.join(node)}. "
            "Значит, результат зависит от узла: фильтр стоит не на всех маршрутах до сервиса или узлы за ним разные "
            "(кэш оператора, другой маршрут). По одному адресу сервиса об остальных судить нельзя."
        )
        findings.append(Finding(protocol, SCOPE_NODE, "warn", f"{title}: зависит от узла", text, tuple(node)))
    dead = [
        ip
        for _host, ip in facts.addresses
        if ip not in control_ok_ips
        and all(rows[name].get(ip) == CUT for name in (*facts.names, *facts.controls))
        and any(state == ANSWERED for row in rows.values() for state in row.values())
    ]
    if protocol == PROTOCOL_QUIC:
        # Молчит только QUIC, а TLS на том же адресе хоть с каким-то именем отвечает: адрес жив. Так ведёт себя
        # кэш провайдера, который QUIC не поддерживает (отчёт с МТС, 15.11.2026), — «закрытым» его называть нельзя.
        tls_rows = [_row(facts, name, PROTOCOL_TLS) for name in (*facts.names, *facts.controls)]
        no_quic = [ip for ip in dead if any(row.get(ip) == ANSWERED for row in tls_rows)]
        dead = [ip for ip in dead if ip not in no_quic]
        if no_quic:
            text = (
                f"{title}: адрес {', '.join(_label(facts, ip) for ip in no_quic)} не отвечает на QUIC ни с каким именем, "
                "хотя по TLS отвечает. Так бывает у кэша провайдера, который QUIC не поддерживает, или когда UDP 443 к нему "
                "закрыт; имя сайта тут ни при чём, и браузер берёт с него данные обычным соединением."
            )
            findings.append(Finding(protocol, SCOPE_ADDRESS, "info", f"{title}: узел без QUIC", text, tuple(no_quic)))
    if dead:
        text = (
            f"{title}: адрес {', '.join(_label(facts, ip) for ip in dead)} не отвечает ни с каким именем, а другие адреса "
            "сервиса отвечают. Закрыт сам адрес (или путь до него), а не имя сайта."
        )
        findings.append(Finding(protocol, SCOPE_ADDRESS, "warn", f"{title}: закрыт адрес", text, tuple(dead)))
    if not findings:
        text = (
            f"{title}: все имена сервиса проходят на всех проверенных адресах так же, как постороннее имя. "
            "Избирательной блокировки по имени, адресу или узлу на этом протоколе не видно."
        )
        findings.append(Finding(protocol, SCOPE_NONE, "ok", f"{title}: избирательности нет", text))
    return findings + tail


def judge(facts: MatrixFacts) -> MatrixVerdict | None:
    """Выводы по таблице. None — проверять нечего или проверку сняли."""
    if facts.cancelled or not facts.addresses or not facts.cells:
        return None
    findings: list[Finding] = []
    for protocol in (PROTOCOL_TLS, PROTOCOL_QUIC):
        findings.extend(_judge_protocol(facts, protocol))
    table = []
    for name in (*facts.names, *facts.controls):
        shown = name or "без имени (контроль)"
        if name == CONTROL_NAME:
            shown = f"{CONTROL_NAME} (контроль)"
        elif name and name == facts.recased:
            shown = f"{name} (контроль: то же имя другими буквами)"
        table.append(
            {
                "name": shown,
                "cells": [
                    {
                        "ip": ip,
                        "tls": facts.cells.get((name, ip), Cell()).tls,
                        "quic": facts.cells.get((name, ip), Cell()).quic,
                        "tls_ms": facts.cells.get((name, ip), Cell()).tls_ms,
                        "quic_ms": facts.cells.get((name, ip), Cell()).quic_ms,
                    }
                    for _host, ip in facts.addresses
                ],
            }
        )
    return MatrixVerdict(tuple(findings), tuple(table), facts.addresses)
