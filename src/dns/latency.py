"""Замер скорости DNS-серверов.

Каждому серверу отправляется настоящий DNS-запрос (UDP, порт 53) и
засекается время до ответа. Популярный домен почти наверняка уже лежит в
кэше сервера, поэтому замер показывает именно дорогу до сервера, а не
скорость его поиска. Из нескольких попыток берётся лучшая: первая часто
медленнее из-за прогрева сети.

Заодно проверяется перехват (``utils.dns_interception``): если запросы
заворачиваются по пути, цифры показывают перехватчик, а не выбранные серверы.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from utils.dns_interception import canary_answered
from utils.dns_wire import FAILURE_OTHER, TYPE_A, query_udp

QUERY_DOMAIN = "google.com"
ATTEMPTS = 3
TIMEOUT_S = 1.0
MAX_PARALLEL = 16


@dataclass(frozen=True, slots=True)
class DnsLatencyReport:
    """Итог замера: {адрес: мс или None} и признак перехвата DNS по пути."""

    results: dict[str, float | None] = field(default_factory=dict)
    intercepted: bool = False


def measure_server_ms(
    server: str,
    *,
    attempts: int = ATTEMPTS,
    timeout_s: float = TIMEOUT_S,
    domain: str = QUERY_DOMAIN,
) -> float | None:
    """Лучшее время ответа сервера в миллисекундах или None, если он молчит."""
    best: float | None = None
    for _attempt in range(max(1, int(attempts))):
        result = query_udp(server, domain, TYPE_A, timeout_s=timeout_s)
        if result.failure == FAILURE_OTHER:
            # Не адрес или не имя: повторять бессмысленно.
            return None
        if result.answered and result.elapsed_ms is not None:
            best = result.elapsed_ms if best is None else min(best, result.elapsed_ms)
    return best


def measure_dns_latency(servers: list[str]) -> DnsLatencyReport:
    """Замеряет все серверы параллельно и заодно проверяет перехват."""
    unique = [server for server in dict.fromkeys(str(item or "").strip() for item in servers) if server]
    if not unique:
        return DnsLatencyReport()
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(unique) + 1)) as pool:
        intercepted = pool.submit(canary_answered, QUERY_DOMAIN, timeout_s=TIMEOUT_S)
        measured = list(pool.map(measure_server_ms, unique))
        return DnsLatencyReport(results=dict(zip(unique, measured)), intercepted=intercepted.result())


__all__ = [
    "DnsLatencyReport",
    "measure_dns_latency",
    "measure_server_ms",
]
