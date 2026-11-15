"""DNS of the network interfaces, asked from the system through ``Platform.dns``.

Thin neutral layer: the page and the runtime import this module, not a system. Tests replace the functions here.
"""

from __future__ import annotations

from typing import Sequence

from platforms import current
from platforms.dns_types import (
    IF_OPER_STATUS_UP,
    IF_TYPE_ETHERNET_CSMACD,
    IF_TYPE_IEEE80211,
    IF_TYPE_SOFTWARE_LOOPBACK,
    DnsBackendError,
    InternetRoute,
    RawInterface,
    StaticDns,
    split_name_servers,
)


def list_interfaces() -> list[RawInterface]:
    return current().dns.list_interfaces()


def internet_route() -> InternetRoute:
    return current().dns.internet_route()


def read_static_dns(guid: str) -> StaticDns:
    return current().dns.read_static_dns(guid)


def write_dns(guid: str, servers: Sequence[str], *, ipv6: bool, doh_templates: dict[str, str] | None = None) -> None:
    current().dns.write_dns(guid, servers, ipv6=ipv6, doh_templates=doh_templates)


def flush_resolver_cache() -> bool:
    return current().dns.flush_resolver_cache()


def is_doh_supported() -> bool:
    return current().dns.is_doh_supported()


__all__ = [
    "DnsBackendError",
    "IF_OPER_STATUS_UP",
    "IF_TYPE_ETHERNET_CSMACD",
    "IF_TYPE_IEEE80211",
    "IF_TYPE_SOFTWARE_LOOPBACK",
    "InternetRoute",
    "RawInterface",
    "StaticDns",
    "flush_resolver_cache",
    "internet_route",
    "is_doh_supported",
    "list_interfaces",
    "read_static_dns",
    "split_name_servers",
    "write_dns",
]
