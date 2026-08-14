"""DNS integrity check — детекция подмены DNS провайдером.

Ключевое решение: **расхождение адресов между системным резолвером и DoH само по
себе уликой не является**. Anycast и geo-DNS крупных CDN (Facebook, x.com,
Google) штатно отдают разным резолверам разные адреса, и прежнее сравнение
множеств IP давало ложную «DNS подмену» на каждом таком домене.

Решает валидность сертификата: подключаемся к полученному адресу с SNI домена и
полной проверкой цепочки. Настоящий сервер предъявит валидный сертификат с любого
своего адреса; заглушка провайдера — нет.
"""

from __future__ import annotations

import logging
import socket
import ssl
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from blockcheck.config import (
    CERT_PROBE_TIMEOUT,
    DEFAULT_PARALLEL,
    DNS_CHECK_DOMAINS,
    DNS_TIMEOUT,
    DNS_UDP_SERVERS,
    DOH_SERVERS,
    DOH_TIMEOUT,
    KNOWN_BLOCK_IPS,
)
from blockcheck.hosts import base_domain
from blockcheck.models import DnsVerdict, DNSIntegrityResult
from utils.concurrency import iter_completed
from utils.net_resolve import resolve_addrinfo

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = [
    "check_dns_integrity",
    "resolve_doh",
    "resolve_udp",
    "verify_certificate",
]


# Сколько DNS-серверов каждого вида опрашиваем, прежде чем сдаться.
_SERVERS_PER_KIND = 2


# ---------------------------------------------------------------------------
# UDP DNS resolution (stdlib fallback — no aiodns required)
# ---------------------------------------------------------------------------

def resolve_udp(domain: str, nameserver: str, timeout: float = DNS_TIMEOUT) -> list[str]:
    """Resolve domain via a specific DNS server using raw UDP socket.

    Builds a minimal DNS query (A record) and parses the response.
    Falls back to socket.getaddrinfo if raw query fails.
    """
    import struct
    import secrets

    try:
        # Build DNS query
        tx_id = secrets.token_bytes(2)
        flags = b"\x01\x00"  # Standard query, recursion desired
        counts = struct.pack(">HHHH", 1, 0, 0, 0)  # 1 question

        # Encode domain name
        qname = b""
        for part in domain.split("."):
            qname += bytes([len(part)]) + part.encode("ascii")
        qname += b"\x00"

        qtype = struct.pack(">H", 1)   # A record
        qclass = struct.pack(">H", 1)  # IN class

        query = tx_id + flags + counts + qname + qtype + qclass

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(query, (nameserver, 53))
        data, _ = sock.recvfrom(1024)
        sock.close()

        # Parse response — skip header and question, read answers
        if len(data) < 12:
            return []

        ans_count = struct.unpack(">H", data[6:8])[0]
        # Skip question section
        offset = 12
        # Skip QNAME
        while offset < len(data) and data[offset] != 0:
            if data[offset] & 0xC0 == 0xC0:  # Pointer
                offset += 2
                break
            offset += data[offset] + 1
        else:
            offset += 1  # null terminator
        offset += 4  # QTYPE + QCLASS

        ips = []
        for _ in range(ans_count):
            if offset + 12 > len(data):
                break
            # Skip NAME (may be pointer)
            if data[offset] & 0xC0 == 0xC0:
                offset += 2
            else:
                while offset < len(data) and data[offset] != 0:
                    offset += data[offset] + 1
                offset += 1

            rtype = struct.unpack(">H", data[offset : offset + 2])[0]
            rdlength = struct.unpack(">H", data[offset + 8 : offset + 10])[0]
            offset += 10

            if rtype == 1 and rdlength == 4:  # A record
                ip = socket.inet_ntoa(data[offset : offset + 4])
                ips.append(ip)
            offset += rdlength

        return ips

    except Exception:
        # Fallback: use system resolver
        try:
            infos = resolve_addrinfo(
                domain, None, timeout=timeout, family=socket.AF_INET,
            )
            return list({info[4][0] for info in infos})
        except Exception:
            return []


# ---------------------------------------------------------------------------
# DoH resolution (requests)
# ---------------------------------------------------------------------------

def resolve_doh(domain: str, doh_url: str, timeout: float = DOH_TIMEOUT) -> list[str]:
    """Resolve domain via DNS-over-HTTPS using requests."""
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed, skipping DoH check")
        return []

    try:
        with requests.Session() as client:
            params = {"name": domain, "type": "A"}
            headers = {"Accept": "application/dns-json"}
            resp = client.get(
                doh_url,
                params=params,
                headers=headers,
                timeout=timeout,
                verify=True,
                allow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()

            ips = []
            for answer in data.get("Answer", []):
                if answer.get("type") == 1:  # A record
                    ips.append(answer["data"])
            return ips
    except Exception as e:
        logger.debug("DoH resolution failed for %s via %s: %s", domain, doh_url, e)
        return []


# ---------------------------------------------------------------------------
# Certificate probe — решающая улика
# ---------------------------------------------------------------------------

def verify_certificate(
    domain: str,
    ip: str,
    timeout: float = CERT_PROBE_TIMEOUT,
) -> bool | None:
    """Валиден ли сертификат домена на этом адресе.

    Returns
    -------
    True
        Сервер предъявил валидную цепочку для ``domain`` — адрес настоящий.
    False
        Сертификат не проходит проверку: на этом адресе сидит не тот сервер.
    None
        Выяснить не удалось (обрыв, таймаут, отказ) — улики нет.
    """
    if not domain or not ip:
        return None

    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    context = ssl.create_default_context()
    sock = socket.socket(family, socket.SOCK_STREAM)
    ssock = None
    try:
        sock.settimeout(timeout)
        ssock = context.wrap_socket(sock, server_hostname=domain)
        ssock.connect((ip, 443))
        return True
    except ssl.SSLCertVerificationError:
        return False
    except Exception:
        # Сброс, таймаут, отказ — это блокировка канала, а не подмена DNS.
        return None
    finally:
        try:
            (ssock or sock).close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DNS integrity check
# ---------------------------------------------------------------------------

def check_dns_integrity(
    domains: list[str] | None = None,
    callback: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    parallel: int = DEFAULT_PARALLEL,
) -> list[DNSIntegrityResult]:
    """Сравнивает системный DNS с DoH и проверяет спорные адреса сертификатом.

    Порядок: резолв (параллельно по доменам) → поиск заглушек по всему набору →
    проверка сертификата только для доменов, где адреса разошлись.
    """
    if domains is None:
        domains = DNS_CHECK_DOMAINS

    def _is_cancelled() -> bool:
        if not callable(cancelled):
            return False
        try:
            return bool(cancelled())
        except Exception:
            return False

    if not domains or _is_cancelled():
        return []

    if callback:
        callback(f"DNS integrity: резолвим {len(domains)} доменов (UDP + DoH)")

    resolved = _resolve_all(domains, parallel=parallel, cancelled=cancelled)
    if _is_cancelled():
        return []

    stub_ips = _detect_stub_ips({d: ips for d, (ips, _) in resolved.items()}, len(domains))

    if callback:
        callback("DNS integrity: проверяем сертификаты спорных адресов")

    results = _judge_all(domains, resolved, stub_ips, parallel=parallel, cancelled=cancelled)

    if callback and not _is_cancelled():
        fake = sum(1 for item in results if item.verdict == DnsVerdict.FAKE)
        unknown = sum(1 for item in results if item.verdict == DnsVerdict.INCONCLUSIVE)
        callback(
            f"DNS integrity: {len(results) - fake - unknown} OK, "
            f"{fake} подмена, {unknown} без вывода"
        )

    return results


def _resolve_all(
    domains: list[str],
    *,
    parallel: int,
    cancelled: Callable[[], bool] | None,
) -> dict[str, tuple[list[str], list[str]]]:
    """Резолвит все домены параллельно: домен → (udp_ips, doh_ips)."""

    def _resolve_one(domain: str) -> tuple[str, tuple[list[str], list[str]]]:
        udp_ips: list[str] = []
        for server in DNS_UDP_SERVERS[:_SERVERS_PER_KIND]:
            udp_ips = resolve_udp(domain, server)
            if udp_ips:
                break

        doh_ips: list[str] = []
        for server in DOH_SERVERS[:_SERVERS_PER_KIND]:
            doh_ips = resolve_doh(domain, server["url"])
            if doh_ips:
                break

        return domain, (udp_ips, doh_ips)

    return _run_pool(domains, _resolve_one, parallel=parallel, cancelled=cancelled)


def _detect_stub_ips(udp_results: dict[str, list[str]], domain_count: int) -> set[str]:
    """Адреса-заглушки: из известного списка или один IP на несвязанных доменах."""
    ip_to_bases: dict[str, set[str]] = {}
    ip_counts: Counter[str] = Counter()
    for domain, ips in udp_results.items():
        base = base_domain(domain)
        for ip in ips:
            ip_counts[ip] += 1
            ip_to_bases.setdefault(ip, set()).add(base)

    required_hits = 3 if domain_count >= 3 else 2
    shared = {
        ip
        for ip, bases in ip_to_bases.items()
        if len(bases) >= required_hits and ip_counts[ip] >= required_hits
    }
    return shared | (set(ip_counts) & KNOWN_BLOCK_IPS)


def _judge_all(
    domains: list[str],
    resolved: dict[str, tuple[list[str], list[str]]],
    stub_ips: set[str],
    *,
    parallel: int,
    cancelled: Callable[[], bool] | None,
) -> list[DNSIntegrityResult]:
    def _judge_one(domain: str) -> tuple[str, DNSIntegrityResult]:
        udp_ips, doh_ips = resolved.get(domain, ([], []))
        return domain, judge_domain(domain, udp_ips, doh_ips, stub_ips)

    judged = _run_pool(domains, _judge_one, parallel=parallel, cancelled=cancelled)
    return [judged[domain] for domain in domains if domain in judged]


def judge_domain(
    domain: str,
    udp_ips: list[str],
    doh_ips: list[str],
    stub_ips: set[str],
    *,
    verify: Callable[[str, str], bool | None] = verify_certificate,
) -> DNSIntegrityResult:
    """Вердикт по одному домену. Сеть трогается только при расхождении адресов."""
    result = DNSIntegrityResult(
        domain=domain,
        udp_ips=list(udp_ips),
        doh_ips=list(doh_ips),
        is_comparable=bool(udp_ips and doh_ips),
    )

    domain_stubs = [ip for ip in udp_ips if ip in stub_ips]
    if domain_stubs:
        result.is_stub = True
        result.stub_ip = domain_stubs[0]
        result.verdict = DnsVerdict.FAKE
        result.is_consistent = False
        result.evidence = f"адрес-заглушка {domain_stubs[0]}"
        return result

    if not udp_ips:
        result.verdict = DnsVerdict.INCONCLUSIVE
        result.is_consistent = False
        result.evidence = "системный DNS не ответил"
        return result

    if doh_ips and (set(udp_ips) & set(doh_ips)):
        result.verdict = DnsVerdict.OK
        result.evidence = "адреса совпадают с DoH"
        return result

    # Адреса разошлись (или сравнивать не с чем) — спрашиваем сертификат.
    udp_verdict = verify(domain, udp_ips[0])
    if udp_verdict is True:
        result.verdict = DnsVerdict.OK
        result.evidence = (
            "адреса отличаются от DoH, но сертификат валиден (anycast/geo-DNS)"
            if doh_ips else "сертификат на адресе системного DNS валиден"
        )
        return result

    if udp_verdict is None:
        result.verdict = DnsVerdict.INCONCLUSIVE
        result.is_consistent = False
        result.evidence = "сертификат проверить не удалось — канал недоступен"
        return result

    # Сертификат невалиден. Если и на адресе от DoH он невалиден — проблема в
    # канале (MITM/перехват), а не в DNS: вывода про подмену не делаем.
    if doh_ips and verify(domain, doh_ips[0]) is not True:
        result.verdict = DnsVerdict.INCONCLUSIVE
        result.is_consistent = False
        result.evidence = "сертификат невалиден на обоих адресах — перехват канала"
        return result

    result.verdict = DnsVerdict.FAKE
    result.is_consistent = False
    result.evidence = "на адресе от провайдера чужой сертификат"
    return result


def _run_pool(
    items: list[str],
    worker: Callable[[str], tuple[str, object]],
    *,
    parallel: int,
    cancelled: Callable[[], bool] | None,
) -> dict:
    """Прогоняет ``worker`` по элементам в пуле, не мешая отмене."""
    collected: dict = {}
    if not items:
        return collected

    pool = ThreadPoolExecutor(max_workers=max(1, min(parallel, len(items))))
    try:
        futures = [pool.submit(worker, item) for item in items]
        for future in iter_completed(futures, cancelled=cancelled):
            try:
                key, value = future.result()
            except Exception:  # noqa: BLE001 — падение одного домена не рушит фазу
                logger.debug("DNS integrity worker failed", exc_info=True)
                continue
            collected[key] = value
    finally:
        # При отмене ждать незавершимые сетевые задачи нельзя.
        pool.shutdown(wait=False, cancel_futures=True)

    return collected
