"""Замер скорости DNS-серверов.

Каждому серверу отправляется настоящий DNS-запрос (UDP, порт 53) и
засекается время до ответа. Популярный домен почти наверняка уже лежит в
кэше сервера, поэтому замер показывает именно дорогу до сервера, а не
скорость его поиска. Из нескольких попыток берётся лучшая: первая часто
медленнее из-за прогрева сети.

Заодно проверяется перехват: запрос уходит и на контрольный адрес из
документационной сети 192.0.2.0/24, где DNS-сервера не бывает. Если
«ответил» и он, значит провайдер (или роутер) подменяет все DNS-запросы
по пути, и цифры показывают его, а не выбранные серверы.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from ipaddress import ip_address

QUERY_DOMAIN = "google.com"
ATTEMPTS = 3
TIMEOUT_S = 1.0
MAX_PARALLEL = 16
# TEST-NET-1 (RFC 5737): настоящего DNS-сервера здесь нет и быть не может.
CANARY_SERVER = "192.0.2.53"


@dataclass(frozen=True, slots=True)
class DnsLatencyReport:
    """Итог замера: {адрес: мс или None} и признак перехвата DNS по пути."""

    results: dict[str, float | None] = field(default_factory=dict)
    intercepted: bool = False


def build_dns_query(query_id: int, domain: str = QUERY_DOMAIN) -> bytes:
    """Собирает DNS-запрос записи A с флагом «рекурсия нужна»."""
    header = struct.pack("!HHHHHH", query_id & 0xFFFF, 0x0100, 1, 0, 0, 0)
    labels = b"".join(
        bytes([len(part)]) + part.encode("ascii")
        for part in domain.strip(".").split(".")
        if part
    )
    return header + labels + b"\x00" + struct.pack("!HH", 1, 1)


def is_dns_answer(packet: bytes, query_id: int) -> bool:
    """Ответ на наш запрос: тот же номер и выставлен бит «это ответ»."""
    if len(packet) < 12:
        return False
    answer_id, flags = struct.unpack("!HH", packet[:4])
    return answer_id == (query_id & 0xFFFF) and bool(flags & 0x8000)


def measure_server_ms(
    server: str,
    *,
    attempts: int = ATTEMPTS,
    timeout_s: float = TIMEOUT_S,
    domain: str = QUERY_DOMAIN,
) -> float | None:
    """Лучшее время ответа сервера в миллисекундах или None, если он молчит."""
    try:
        family = socket.AF_INET6 if ip_address(server).version == 6 else socket.AF_INET
    except ValueError:
        return None

    best: float | None = None
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError:
        return None
    with sock:
        sock.settimeout(timeout_s)
        for _ in range(max(1, int(attempts))):
            query_id = int.from_bytes(os.urandom(2), "big")
            try:
                started = time.perf_counter()
                sock.sendto(build_dns_query(query_id, domain), (server, 53))
                deadline = started + timeout_s
                while True:
                    packet, _addr = sock.recvfrom(4096)
                    if is_dns_answer(packet, query_id):
                        elapsed = (time.perf_counter() - started) * 1000.0
                        best = elapsed if best is None else min(best, elapsed)
                        break
                    # Чужой или опоздавший ответ: ждём свой, пока есть время.
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    sock.settimeout(remaining)
            except OSError:
                pass
            finally:
                sock.settimeout(timeout_s)
    return best


def measure_dns_latency(servers: list[str]) -> DnsLatencyReport:
    """Замеряет все серверы параллельно вместе с контрольным адресом."""
    unique = [server for server in dict.fromkeys(str(item or "").strip() for item in servers) if server]
    if not unique:
        return DnsLatencyReport()
    probes = [*unique, CANARY_SERVER]
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(probes))) as pool:
        measured = list(pool.map(measure_server_ms, probes))
    return DnsLatencyReport(
        results=dict(zip(unique, measured[:-1])),
        intercepted=measured[-1] is not None,
    )


__all__ = [
    "CANARY_SERVER",
    "DnsLatencyReport",
    "build_dns_query",
    "is_dns_answer",
    "measure_dns_latency",
    "measure_server_ms",
]
