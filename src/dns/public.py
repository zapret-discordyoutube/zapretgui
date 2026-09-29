from __future__ import annotations

from dns.commands import (
    apply_auto_dns,
    apply_dns_on_startup_async,
    apply_provider_dns,
    check_ipv6_connectivity,
    flush_dns_cache,
    get_network_adapters_native,
    get_dns_state,
    is_isp_dns_warning_shown,
    load_page_data,
    mark_isp_dns_warning_shown,
    migrate_outdated_dns_addresses,
    normalize_adapter_alias,
    refresh_dns_info,
    measure_dns_latency,
    consume_warmed_page_data,
    warm_page_data_cache,
)
from dns.dns_providers import DNS_PROVIDERS
from dns.state import DnsCommandResult, DnsState


__all__ = [
    "DNS_PROVIDERS",
    "DnsCommandResult",
    "DnsState",
    "apply_auto_dns",
    "apply_dns_on_startup_async",
    "apply_provider_dns",
    "check_ipv6_connectivity",
    "flush_dns_cache",
    "get_network_adapters_native",
    "get_dns_state",
    "is_isp_dns_warning_shown",
    "load_page_data",
    "mark_isp_dns_warning_shown",
    "migrate_outdated_dns_addresses",
    "normalize_adapter_alias",
    "refresh_dns_info",
    "measure_dns_latency",
    "consume_warmed_page_data",
    "warm_page_data_cache",
]
