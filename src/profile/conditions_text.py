"""Условия profile словами: сводка в шапке и пояснения к диапазонам пакетов.

Текст пресета не меняется: это только показ того, что в нём уже записано.
"""

from __future__ import annotations

import re

from profile.match_filters import ports_label_from_match_lines, protocol_label_from_match_lines


_FIRST_PACKETS_RE = re.compile(r"^-(?P<mode>[nd])(?P<count>\d+)$", re.IGNORECASE)
_PLAIN_PROTOCOLS = frozenset({"TCP", "UDP", "TCP/UDP"})


def _first_packets_phrase(count: int, *, with_data: bool) -> str:
    tail = " с данными" if with_data else ""
    last_two = count % 100
    last = count % 10
    if last == 1 and last_two != 11:
        return f"первый {count} пакет{tail}" if count != 1 else f"первый пакет{tail}"
    if last in (2, 3, 4) and last_two not in (12, 13, 14):
        return f"первые {count} пакета{tail}"
    return f"первые {count} пакетов{tail}"


def range_phrase(expression: str) -> str:
    """Диапазон winws2 словами: `-d8` → «первые 8 пакетов с данными»."""
    expr = str(expression or "").strip().lower()
    if not expr:
        return ""
    if expr == "a":
        return "все пакеты"
    if expr == "x":
        return "не обрабатываются"
    match = _FIRST_PACKETS_RE.match(expr)
    if match:
        return _first_packets_phrase(int(match.group("count")), with_data=match.group("mode").lower() == "d")
    return expr


def range_hint(mode: str, value: str) -> str:
    """Пояснение под полем диапазона в панели условий."""
    mode = str(mode or "").strip().lower()
    value = str(value or "").strip()
    if mode == "a":
        return "Стратегия работает на всех пакетах соединения."
    if mode == "x":
        return "Пакеты в эту сторону стратегия не трогает."
    if mode in {"n", "d"}:
        if not value.isdigit():
            return "Укажите число: сколько первых пакетов соединения обрабатывать."
        phrase = _first_packets_phrase(int(value), with_data=mode == "d")
        if mode == "d":
            return f"Только {phrase}. Служебные пакеты без данных не считаются."
        return f"Только {phrase} соединения, считая служебные."
    if mode == "custom":
        return "Выражение winws2 записывается как есть, например s1<d1 или -d8."
    return ""


def _match_label(match_lines: tuple[str, ...]) -> str:
    protocol = protocol_label_from_match_lines(match_lines)
    ports = ports_label_from_match_lines(match_lines)
    # «TCP • TCP 80,443» — одно и то же дважды: порты уже называют протокол.
    if ports and protocol in _PLAIN_PROTOCOLS:
        return ports
    return " · ".join(part for part in (protocol, ports) if part)


def conditions_summary(
    *,
    match_lines: tuple[str, ...],
    match_summary: str,
    filter_value: str,
    in_range: str,
    out_range: str,
) -> str:
    """Одна строка в шапке profile: что он ловит и на каких пакетах работает."""
    lines = tuple(str(line or "") for line in (match_lines or ()))
    parts = [_match_label(lines) if lines else str(match_summary or "").strip()]
    parts.append(str(filter_value or "").strip())

    out_phrase = range_phrase(out_range)
    if out_phrase:
        parts.append("исходящие не обрабатываются" if out_phrase == "не обрабатываются" else out_phrase)
    in_expr = str(in_range or "").strip().lower()
    if in_expr and in_expr != "x":
        parts.append(f"входящие: {range_phrase(in_expr)}")
    return " · ".join(part for part in parts if part)
