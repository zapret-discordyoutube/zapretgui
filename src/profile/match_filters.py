from __future__ import annotations

from typing import Iterable, Literal


# Протоколы из --filter-l7, которые ходят только поверх TCP. Всё остальное
# (quic, stun, discord, wireguard, dht, dtls, неизвестные имена) считаем UDP.
_TCP_L7: frozenset[str] = frozenset({"http", "tls", "xmpp", "mtproto", "bt"})


def l7_transport(values: Iterable[str]) -> Literal["tcp", "udp"] | None:
    """Транспорт для значений --filter-l7: "tcp", если все имена TCP-протоколы.

    Значения можно передавать как есть из строк фильтра ("http,tls").
    Без имён возвращает None; хотя бы одно не-TCP имя даёт "udp".
    """
    names = {
        token.strip().lower()
        for value in values
        for token in str(value or "").split(",")
        if token.strip()
    }
    if not names:
        return None
    return "tcp" if names <= _TCP_L7 else "udp"


def strategy_catalog_from_match_lines(match_lines: tuple[str, ...]) -> str:
    if is_voice_match(match_lines):
        return "voice"
    has_tcp = bool(filter_values(match_lines, "--filter-tcp"))
    has_udp = bool(filter_values(match_lines, "--filter-udp"))
    if has_udp and not has_tcp:
        return "udp"
    if has_tcp and not has_udp:
        return "http80" if is_http80_match(match_lines) else "tcp"
    return l7_transport(filter_values(match_lines, "--filter-l7")) or "tcp"


def protocol_label_from_match_lines(match_lines: tuple[str, ...]) -> str:
    has_l7 = bool(filter_values(match_lines, "--filter-l7"))
    has_udp = bool(filter_values(match_lines, "--filter-udp"))
    has_tcp = bool(filter_values(match_lines, "--filter-tcp"))
    if is_voice_match(match_lines):
        return "Voice"
    if has_tcp and (has_udp or has_l7):
        return "TCP/UDP"
    if has_l7:
        return "L7"
    if has_udp:
        return "UDP"
    if is_http80_match(match_lines):
        return "TCP/HTTP"
    return "TCP"


def ports_label_from_match_lines(match_lines: tuple[str, ...]) -> str:
    parts: list[str] = []
    for label, option_name in (("TCP", "--filter-tcp"), ("UDP", "--filter-udp")):
        values = filter_values(match_lines, option_name)
        if values:
            parts.append(f"{label} {', '.join(values)}")
    return "; ".join(parts)


def filter_values(match_lines: tuple[str, ...], option_name: str) -> tuple[str, ...]:
    prefix = option_name.lower().rstrip("=") + "="
    return tuple(
        line.split("=", 1)[1].strip()
        for line in match_lines
        if line.lower().startswith(prefix) and "=" in line
    )


def is_voice_match(match_lines: tuple[str, ...]) -> bool:
    l7_values = ",".join(filter_values(match_lines, "--filter-l7")).lower()
    if any(token in l7_values for token in ("stun", "discord", "wireguard")):
        return True
    udp_values = filter_values(match_lines, "--filter-udp")
    match_text = " ".join(match_lines).lower()
    if not any(token in match_text for token in ("discord", "stun", "voice", "голос")):
        return False
    return any(_ports_overlap(value, 50000, 59000) for value in udp_values)


def is_http80_match(match_lines: tuple[str, ...]) -> bool:
    tcp_values = filter_values(match_lines, "--filter-tcp")
    if not tcp_values or filter_values(match_lines, "--filter-udp"):
        return False
    if l7_transport(filter_values(match_lines, "--filter-l7")) == "udp":
        return False
    ports = _parse_ports(",".join(tcp_values))
    return bool(ports) and ports == {80}


def _ports_overlap(raw_ports: str, start: int, end: int) -> bool:
    for token in str(raw_ports or "").split(","):
        bounds = _parse_port_token(token)
        if bounds is None:
            continue
        token_start, token_end = bounds
        if token_start <= end and token_end >= start:
            return True
    return False


def _parse_ports(raw_ports: str) -> set[int]:
    ports: set[int] = set()
    for token in str(raw_ports or "").split(","):
        bounds = _parse_port_token(token)
        if bounds is None:
            continue
        start, end = bounds
        if start == end:
            ports.add(start)
            continue
        if end - start <= 512:
            ports.update(range(start, end + 1))
        else:
            ports.add(start)
            ports.add(end)
    return ports


def _parse_port_token(token: str) -> tuple[int, int] | None:
    stripped = str(token or "").strip()
    if not stripped:
        return None
    if "-" in stripped:
        left, _, right = stripped.partition("-")
        if not left.strip().isdigit() or not right.strip().isdigit():
            return None
        start = int(left.strip())
        end = int(right.strip())
        if start > end:
            start, end = end, start
        return start, end
    if not stripped.isdigit():
        return None
    port = int(stripped)
    return port, port
