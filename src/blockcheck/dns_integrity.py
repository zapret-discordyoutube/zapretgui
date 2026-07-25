"""DNS integrity check — compare UDP DNS vs DoH to detect faking/stubs."""

from __future__ import annotations

import logging
import socket
from collections import Counter
from typing import TYPE_CHECKING

from blockcheck.config import (
    DNS_CHECK_DOMAINS,
    DNS_TIMEOUT,
    DNS_UDP_SERVERS,
    DOH_SERVERS,
    DOH_TIMEOUT,
)
from blockcheck.models import DNSIntegrityResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UDP DNS resolution (stdlib fallback — no aiodns required)
# ---------------------------------------------------------------------------

def _resolve_udp(domain: str, nameserver: str, timeout: float = DNS_TIMEOUT) -> list[str]:
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
            infos = socket.getaddrinfo(domain, None, socket.AF_INET)
            return list({info[4][0] for info in infos})
        except Exception:
            return []


# ---------------------------------------------------------------------------
# DoH resolution (httpx)
# ---------------------------------------------------------------------------

def _resolve_doh(domain: str, doh_url: str, timeout: float = DOH_TIMEOUT) -> list[str]:
    """Resolve domain via DNS-over-HTTPS using httpx."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed, skipping DoH check")
        return []

    try:
        with httpx.Client(timeout=timeout, verify=True, follow_redirects=True) as client:
            params = {"name": domain, "type": "A"}
            headers = {"Accept": "application/dns-json"}
            resp = client.get(doh_url, params=params, headers=headers)
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
# DNS integrity check
# ---------------------------------------------------------------------------

def check_dns_integrity(
    domains: list[str] | None = None,
    callback: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[DNSIntegrityResult]:
    """Compare UDP DNS vs DoH results to detect DNS faking/stubs.

    Strategy:
    1. Resolve each domain via first available UDP DNS server
    2. Resolve each domain via first available DoH server
    3. Compare results — if UDP returns IPs that DoH doesn't, flag as fake
    4. Detect stub IPs (same IP appearing across multiple unrelated base domains)
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

    if callback and not _is_cancelled():
        callback("DNS integrity: resolving via UDP...")

    # Phase 1: UDP DNS
    udp_results: dict[str, list[str]] = {}
    for domain in domains:
        if _is_cancelled():
            break
        ips = []
        for server in DNS_UDP_SERVERS[:2]:  # Use first 2 servers
            if _is_cancelled():
                break
            ips = _resolve_udp(domain, server)
            if ips:
                break
        udp_results[domain] = ips

    if _is_cancelled():
        return []

    if callback:
        callback("DNS integrity: resolving via DoH...")

    # Phase 2: DoH
    doh_results: dict[str, list[str]] = {}
    for domain in domains:
        if _is_cancelled():
            break
        ips = []
        for server in DOH_SERVERS[:2]:
            if _is_cancelled():
                break
            ips = _resolve_doh(domain, server["url"])
            if ips:
                break
        doh_results[domain] = ips

    if _is_cancelled():
        return []

    if callback:
        callback("DNS integrity: analyzing results...")

    # Phase 3: Detect stub IPs by diversity of unrelated base domains.
    # A shared IP on sibling domains (e.g. telegram.org + web.telegram.org) is legitimate.
    public_suffix_2level = {
        "co.uk", "org.uk", "ac.uk", "gov.uk",
        "com.au", "net.au", "org.au",
        "co.jp", "ne.jp", "or.jp",
        "com.br", "com.mx", "com.tr", "co.id",
        "com.ua", "co.kr", "co.in",
    }

    def _base_domain(domain: str) -> str:
        host = domain.strip().lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".")
        if len(parts) <= 2:
            return host

        tail2 = ".".join(parts[-2:])
        if tail2 in public_suffix_2level and len(parts) >= 3:
            return ".".join(parts[-3:])
        return tail2

    ip_to_bases: dict[str, set[str]] = {}
    ip_counts = Counter()
    for domain, ips in udp_results.items():
        base = _base_domain(domain)
        for ip in ips:
            ip_counts[ip] += 1
            ip_to_bases.setdefault(ip, set()).add(base)

    required_base_hits = 3 if len(domains) >= 3 else 2
    stub_ips = {
        ip
        for ip, bases in ip_to_bases.items()
        if len(bases) >= required_base_hits and ip_counts[ip] >= required_base_hits
    }

    # Phase 4: Build results
    results = []
    for domain in domains:
        if _is_cancelled():
            break
        udp_ips = udp_results.get(domain, [])
        doh_ips = doh_results.get(domain, [])

        # Check if UDP result is a known stub
        domain_stub_ips = [ip for ip in udp_ips if ip in stub_ips]
        is_stub = bool(domain_stub_ips)

        is_comparable = bool(udp_ips and doh_ips)

        # Check consistency (at least one UDP IP should match DoH)
        if is_comparable:
            is_consistent = bool(set(udp_ips) & set(doh_ips))
        elif udp_ips and not doh_ips:
            is_consistent = True  # DoH unavailable, not enough data to compare
        else:
            is_consistent = False

        results.append(DNSIntegrityResult(
            domain=domain,
            udp_ips=udp_ips,
            doh_ips=doh_ips,
            is_comparable=is_comparable,
            is_consistent=is_consistent and not is_stub,
            is_stub=is_stub,
            stub_ip=domain_stub_ips[0] if domain_stub_ips else None,
        ))

    return results
