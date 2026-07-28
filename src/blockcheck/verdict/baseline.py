"""Контрольная группа — опорная точка для всех выводов отчёта.

Без неё диагностика не отличает «сайт заблокирован» от «интернета нет» и
«ICMP режется на этой машине». Проверяем несколько нейтральных хостов, которые
не блокируются и не участвуют в целевом списке, и выясняем, какие каналы вообще
работают. Стоит ~1-2 секунды: все пробы идут одним пулом.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from blockcheck.config import BASELINE_TIMEOUT, DOH_SERVERS
from blockcheck.isp_page_detector import check_http_injection
from blockcheck.models import NetworkBaseline, TestStatus
from blockcheck.ping_tester import ping_host
from blockcheck.tls_tester import test_https
from utils.concurrency import iter_completed

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["CONTROL_HOSTS", "probe_baseline"]


# Нейтральные хосты: не блокируются, не пересекаются с целевым списком и не
# используют geo-DNS-трюков, из-за которых проба стала бы нестабильной.
CONTROL_HOSTS: tuple[str, ...] = (
    "example.com",
    "www.msftconnecttest.com",
    "ya.ru",
)


def probe_baseline(
    *,
    timeout: int = BASELINE_TIMEOUT,
    cancelled: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> NetworkBaseline:
    """Пробует контрольные хосты и возвращает срез доступных каналов."""
    results: dict[str, list[bool]] = {
        "tls": [], "http80": [], "icmp": [], "ipv6": [], "doh": [],
    }

    def _tls(host: str, family: str) -> tuple[str, bool]:
        result = test_https(host, timeout=timeout, ip_family=family)
        key = "ipv6" if family == "ipv6" else "tls"
        return key, result.status == TestStatus.OK

    def _http80(host: str) -> tuple[str, bool]:
        result = check_http_injection(host, timeout=timeout)
        # HTTP_INJECT означает, что провайдер ответил вместо сервера: связь есть,
        # но канал :80 доверия не заслуживает.
        return "http80", result.status == TestStatus.OK

    def _icmp(host: str) -> tuple[str, bool]:
        return "icmp", ping_host(host, timeout=timeout).status == TestStatus.OK

    def _doh() -> tuple[str, bool]:
        from blockcheck.dns_integrity import resolve_doh

        return "doh", bool(resolve_doh(CONTROL_HOSTS[0], DOH_SERVERS[0]["url"], timeout=timeout))

    jobs: list[Callable[[], tuple[str, bool]]] = [_doh]
    for host in CONTROL_HOSTS:
        jobs.append(lambda h=host: _tls(h, "ipv4"))
        jobs.append(lambda h=host: _http80(h))
        jobs.append(lambda h=host: _icmp(h))
    # IPv6 проверяем на одном хосте: цель — узнать, есть ли связность вообще,
    # а не измерить каждый хост.
    jobs.append(lambda: _tls(CONTROL_HOSTS[0], "ipv6"))

    pool = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        futures = [pool.submit(job) for job in jobs]
        for future in iter_completed(futures, cancelled=cancelled):
            try:
                key, ok = future.result()
            except Exception:  # noqa: BLE001 — падение одной пробы не рушит baseline
                logger.debug("Baseline probe failed", exc_info=True)
                continue
            results[key].append(ok)
    finally:
        # Ждать зависшие сетевые пробы нельзя: именно это подвешивало BlockCheck.
        pool.shutdown(wait=False, cancel_futures=True)

    tls_ok = any(results["tls"])
    http80_usable = any(results["http80"])
    baseline = NetworkBaseline(
        probed=True,
        internet_ok=tls_ok or http80_usable,
        tls_ok=tls_ok,
        icmp_usable=any(results["icmp"]),
        ipv6_usable=any(results["ipv6"]),
        http80_usable=http80_usable,
        doh_usable=any(results["doh"]),
        control_hosts=list(CONTROL_HOSTS),
    )
    baseline.detail = _describe(baseline)

    if log:
        log(f"Baseline: {baseline.detail}")
    return baseline


def _describe(baseline: NetworkBaseline) -> str:
    if not baseline.internet_ok:
        return "контрольные хосты недоступны — сети нет или она полностью фильтруется"

    channels = [
        ("TLS", baseline.tls_ok),
        ("HTTP :80", baseline.http80_usable),
        ("ICMP", baseline.icmp_usable),
        ("IPv6", baseline.ipv6_usable),
        ("DoH", baseline.doh_usable),
    ]
    available = ", ".join(name for name, ok in channels if ok) or "нет"
    missing = ", ".join(name for name, ok in channels if not ok)
    text = f"доступно: {available}"
    if missing:
        text += f"; недоступно: {missing}"
    return text
