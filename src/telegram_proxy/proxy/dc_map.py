# telegram_proxy/dc_map.py
"""Адреса датацентров Telegram: IP -> DC, прямые TCP-адреса, диапазоны Telegram.

Маршруты WSS (релеи, фронты, туннель) описаны в route_catalog.py и routes.py.
"""

from ipaddress import IPv4Network, IPv4Address, IPv6Network, IPv6Address


# Mapping: IP -> (dc_id, is_media)
# Used to determine DC when MTProto init packet parsing fails
# (e.g., Android clients with useSecret=0 that have random dc_id bytes)
IP_TO_DC: dict[str, tuple[int, bool]] = {
    # DC1
    "149.154.175.50": (1, False), "149.154.175.51": (1, False),
    "149.154.175.53": (1, False), "149.154.175.54": (1, False),
    "149.154.175.52": (1, True),
    # DC2
    "149.154.167.41": (2, False), "149.154.167.50": (2, False),
    "149.154.167.51": (2, False), "149.154.167.220": (2, False),
    "95.161.76.100": (2, False),
    "149.154.167.151": (2, True), "149.154.167.222": (2, True),
    "149.154.167.223": (2, True), "149.154.162.123": (2, True),
    # DC3
    "149.154.175.100": (3, False), "149.154.175.101": (3, False),
    "149.154.175.102": (3, True),
    # DC4
    "149.154.167.91": (4, False), "149.154.167.92": (4, False),
    "149.154.164.250": (4, True), "149.154.166.120": (4, True),
    "149.154.166.121": (4, True), "149.154.167.118": (4, True),
    "149.154.165.111": (4, True),
    # DC5
    "91.108.56.100": (5, False), "91.108.56.101": (5, False),
    "91.108.56.116": (5, False), "91.108.56.126": (5, False),
    "149.154.171.5": (5, False),
    "91.108.56.102": (5, True), "91.108.56.128": (5, True),
    "91.108.56.151": (5, True),
    # DC203
    "91.105.192.100": (203, False),
}

# Direct TCP fallback endpoints (same DCs, raw TCP)
TCP_ENDPOINTS = {
    1: ("149.154.175.50", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.91", 443),
    5: ("91.108.56.100", 443),
    203: ("91.105.192.100", 443),
}

TCP_MEDIA_ENDPOINTS = {
    1: ("149.154.175.52", 443),
    2: ("149.154.167.151", 443),
    3: ("149.154.175.102", 443),
    4: ("149.154.164.250", 443),
    5: ("91.108.56.102", 443),
    203: ("91.105.192.100", 443),
}

# Telegram CIDR ranges -> DC mapping
# Source: https://core.telegram.org/resources/cidr.txt + known DC assignments
_SUBNET_TO_DC: list[tuple[IPv4Network, int]] = [
    # DC2 subnets
    (IPv4Network("91.108.4.0/22"), 2),
    (IPv4Network("91.105.192.0/23"), 2),
    (IPv4Network("185.76.151.0/24"), 2),
    # DC1 subnets
    (IPv4Network("91.108.20.0/22"), 1),
    # DC4 subnets
    (IPv4Network("91.108.8.0/22"), 4),
    (IPv4Network("91.108.12.0/22"), 4),
    # DC5 subnets
    (IPv4Network("91.108.16.0/22"), 5),
    (IPv4Network("91.108.56.0/22"), 5),
    # Large block covering DC1-DC5 (149.154.160.0/20)
    # Most specific /24 ranges first (known DC assignments):
    (IPv4Network("149.154.175.0/24"), 1),   # DC1 primary (175.50-54)
    (IPv4Network("149.154.167.0/24"), 2),   # DC2 primary (167.50-54)
    # Broader /22 ranges:
    (IPv4Network("149.154.160.0/22"), 1),   # DC1
    (IPv4Network("149.154.164.0/22"), 4),   # DC4 (164-167, but 167 overridden above)
    (IPv4Network("149.154.168.0/22"), 2),   # DC2
    (IPv4Network("149.154.172.0/22"), 1),   # DC1/DC3 range, default DC1
    # Fallback for entire /20 block
    (IPv4Network("149.154.160.0/20"), 2),
]

# All Telegram IPv4 CIDR ranges (for checking if IP is Telegram)
TELEGRAM_CIDRS: list[IPv4Network] = [
    IPv4Network("91.108.56.0/22"),
    IPv4Network("91.108.4.0/22"),
    IPv4Network("91.108.8.0/22"),
    IPv4Network("91.108.16.0/22"),
    IPv4Network("91.108.12.0/22"),
    IPv4Network("149.154.160.0/20"),
    IPv4Network("91.105.192.0/23"),
    IPv4Network("91.108.20.0/22"),
    IPv4Network("185.76.151.0/24"),
]

# Telegram IPv6 CIDR ranges
TELEGRAM_V6_CIDRS: list[IPv6Network] = [
    IPv6Network("2001:67c:4e8::/48"),
    IPv6Network("2001:b28:f23c::/46"),
    IPv6Network("2a0a:f280::/32"),
]

# IPv6 DC mapping (prefix -> dc)
_V6_SUBNET_TO_DC: list[tuple[IPv6Network, int]] = [
    (IPv6Network("2001:67c:4e8:f002::/64"), 2),   # DC2
    (IPv6Network("2001:67c:4e8:f003::/64"), 3),   # DC3
    (IPv6Network("2001:67c:4e8:f004::/64"), 4),   # DC4
    (IPv6Network("2001:67c:4e8:f001::/64"), 1),   # DC1
    (IPv6Network("2001:67c:4e8:f005::/64"), 5),   # DC5
    (IPv6Network("2001:b28:f23d:f003::/64"), 3),   # DC3 alt
    (IPv6Network("2001:b28:f23f:f005::/64"), 5),   # DC5 alt
    (IPv6Network("2a0a:f280:203::/48"), 203),       # CDN DC203
    # Fallback for entire ranges
    (IPv6Network("2001:67c:4e8::/48"), 2),
    (IPv6Network("2001:b28:f23c::/46"), 2),
    (IPv6Network("2a0a:f280::/32"), 2),
]

# Pre-compiled set of (network_int, mask) for fast lookup
_COMPILED_NETS: list[tuple[int, int, int]] = []  # (net_addr, mask, dc)


def _compile() -> None:
    """Pre-compile CIDR ranges for fast integer matching."""
    global _COMPILED_NETS
    if _COMPILED_NETS:
        return
    # Sort by prefix length descending (most specific first)
    sorted_subnets = sorted(_SUBNET_TO_DC, key=lambda x: x[0].prefixlen, reverse=True)
    for net, dc in sorted_subnets:
        net_int = int(net.network_address)
        mask = int(net.netmask)
        _COMPILED_NETS.append((net_int, mask, dc))


def ip_to_dc(ip: str) -> int:
    """Map a Telegram IP address to its datacenter number.

    Returns DC number (1-5). Falls back to DC2 (most common) if unknown.
    Supports both IPv4 and IPv6.
    """
    if ":" in ip:
        try:
            addr = IPv6Address(ip)
            for net, dc in _V6_SUBNET_TO_DC:
                if addr in net:
                    return dc
        except ValueError:
            pass
        return 2
    _compile()
    try:
        ip_int = int(IPv4Address(ip))
    except ValueError:
        return 2
    for net_addr, mask, dc in _COMPILED_NETS:
        if (ip_int & mask) == net_addr:
            return dc
    return 2  # Default DC


def is_telegram_ip(ip: str) -> bool:
    """Адрес из официальных диапазонов Telegram (core.telegram.org/resources/cidr.txt)."""
    try:
        if ":" in ip:
            addr6 = IPv6Address(ip)
            return any(addr6 in net for net in TELEGRAM_V6_CIDRS)
        addr = IPv4Address(ip)
    except ValueError:
        return False
    return any(addr in net for net in TELEGRAM_CIDRS)


def parse_dc_endpoint_overrides(value: object) -> dict[int, str]:
    """Parse user DC -> IP overrides like "2:149.154.167.220"."""
    if isinstance(value, str):
        raw_items = value.replace(",", " ").replace(";", " ").split()
    elif isinstance(value, (list, tuple, set)):
        raw_items = []
        for item in value:
            if isinstance(item, str):
                raw_items.extend(item.replace(",", " ").replace(";", " ").split())
    else:
        raw_items = []

    overrides: dict[int, str] = {}
    for item in raw_items:
        text = item.strip()
        if ":" not in text:
            continue
        dc_text, ip_text = text.split(":", 1)
        try:
            dc = int(dc_text.strip())
            ip = str(IPv4Address(ip_text.strip()))
        except Exception:
            continue
        if dc not in {1, 2, 3, 4, 5, 203}:
            continue
        overrides[dc] = ip
    return overrides


def dc_to_tcp_endpoint(
    dc: int,
    overrides: dict[int, str] | None = None,
    *,
    is_media: bool = False,
) -> tuple[str, int]:
    """Get direct TCP endpoint for a datacenter (fallback)."""
    override_ip = (overrides or {}).get(int(dc))
    if override_ip:
        return override_ip, 443
    if is_media:
        return TCP_MEDIA_ENDPOINTS.get(dc, TCP_MEDIA_ENDPOINTS[2])
    return TCP_ENDPOINTS.get(dc, TCP_ENDPOINTS[2])
