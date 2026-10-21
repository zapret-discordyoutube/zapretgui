"""Избирательность блокировки: имя × адрес × протокол, с контролями.

Блокировка редко бывает «всё или ничего». Фильтр на пути может читать имя сайта
(поле SNI в TLS, то же имя в первом пакете QUIC), смотреть на адрес узла или на
протокол и пропускать остальное. Вывод по одному адресу при этом остаётся
догадкой: у сайта открывается главная страница, а видеоузел режут; QUIC к одному
имени теряется, к другому на том же сервере проходит.

Здесь одна таблица замеров. Строки — имена (имена сайтов сервиса и два контроля:
постороннее имя и приветствие без имени), столбцы — адреса сервисов, ячейки — исход
приветствия TLS и первого пакета QUIC. Контроль важен: если с посторонним именем
или без имени тот же адрес отвечает, адрес жив и режут именно имя; если не
отвечает ничего, закрыт адрес или протокол.

Сбор (``collect``) отделён от вывода (``judge``): вывод — чистая функция над таблицей.
Пробы отправляются только по сигналу беды (``needs_matrix``): браузер их не шлёт.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "CONTROL_NAME",
    "MAX_ADDRESSES",
    "MAX_NAMES",
    "SCOPE_ADDRESS",
    "SCOPE_NAME",
    "SCOPE_NODE",
    "SCOPE_NONE",
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

ANSWERED = "answered"
CUT = "cut"
UNKNOWN = "unknown"

PROTOCOL_TLS = "tls"
PROTOCOL_QUIC = "quic"
PROTOCOL_TITLES = {PROTOCOL_TLS: "TLS поверх TCP 443", PROTOCOL_QUIC: "QUIC поверх UDP 443"}

SCOPE_NAME = "name"
SCOPE_ADDRESS = "address"
SCOPE_NODE = "node"
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


@dataclass(frozen=True, slots=True)
class MatrixFacts:
    # Имена сайтов сервиса (без контролей) и адреса, на которых они проверялись: (подпись, IP).
    names: tuple[str, ...]
    addresses: tuple[tuple[str, str], ...]
    # Ключ ячейки — (имя, IP); имя "" — приветствие без имени, ``CONTROL_NAME`` — посторонее имя.
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    cancelled: bool = False


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
    keys = [(name, ip) for name in (*names, CONTROL_NAME, "") for _host, ip in addresses]
    futures = {}
    for name, ip in keys:
        if should_stop():
            return MatrixFacts(names, addresses, {}, cancelled=True)
        futures[(name, ip)] = submit(probe, name, ip)
    cells = {key: future.result() for key, future in futures.items()}
    return MatrixFacts(names, addresses, cells, cancelled=should_stop())


def _row(facts: MatrixFacts, name: str, protocol: str) -> dict[str, str]:
    return {ip: facts.cells.get((name, ip), Cell()).state(protocol) for _host, ip in facts.addresses}


def _label(facts: MatrixFacts, ip: str) -> str:
    hosts = [host for host, address in facts.addresses if address == ip]
    return f"{hosts[0]} ({ip})" if hosts else ip


def _names_word(names: Sequence[str]) -> str:
    return ", ".join(names)


def _judge_protocol(facts: MatrixFacts, protocol: str) -> list[Finding]:
    title = PROTOCOL_TITLES[protocol]
    rows = {name: _row(facts, name, protocol) for name in (*facts.names, CONTROL_NAME, "")}
    if not any(state == ANSWERED for row in rows.values() for state in row.values()):
        # Не ответило ничего: протокол к этим серверам не работает совсем (или не поддержан) — имя тут ни при чём.
        if all(state == CUT for row in rows.values() for state in row.values()):
            text = (
                f"{title}: ни одно сочетание имени и адреса не ответило, даже с посторонним именем и без имени. "
                "Это не блокировка по имени: протокол не доходит до серверов вовсе (закрыт порт, "
                "серверы его не поддерживают или режут весь протокол). Избирательность не определить."
            )
            return [Finding(protocol, SCOPE_ADDRESS, "info", f"{title}: не отвечает совсем", text)]
        return []
    findings: list[Finding] = []
    by_name: dict[str, list[str]] = {}
    control_ok_ips = {
        ip for _host, ip in facts.addresses if rows[CONTROL_NAME].get(ip) == ANSWERED or rows[""].get(ip) == ANSWERED
    }
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
        and all(rows[name].get(ip) == CUT for name in (*facts.names, CONTROL_NAME, ""))
        and any(state == ANSWERED for row in rows.values() for state in row.values())
    ]
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
    return findings


def judge(facts: MatrixFacts) -> MatrixVerdict | None:
    """Выводы по таблице. None — проверять нечего или проверку сняли."""
    if facts.cancelled or not facts.addresses or not facts.cells:
        return None
    findings: list[Finding] = []
    for protocol in (PROTOCOL_TLS, PROTOCOL_QUIC):
        findings.extend(_judge_protocol(facts, protocol))
    table = []
    for name in (*facts.names, CONTROL_NAME, ""):
        shown = name or "без имени (контроль)"
        if name == CONTROL_NAME:
            shown = f"{CONTROL_NAME} (контроль)"
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
