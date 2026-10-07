from __future__ import annotations

from dns.commands import (
    apply_dns,
    consume_warmed_state,
    delete_custom_server,
    duplicate_custom_server,
    flush_dns_cache,
    is_isp_dns_warning_shown,
    load_state,
    mark_isp_dns_warning_shown,
    measure_dns_latency,
    migrate_outdated_dns_addresses,
    repair_local_proxy,
    reset_to_auto,
    save_custom_server,
    start_local_proxy,
    stop_local_proxy_if_unused,
    warm_state,
)
from dns.dns_providers import DNS_PROVIDERS
from dns.state import CustomServerResult, DnsCommandResult, DnsState


__all__ = [
    "DNS_PROVIDERS",
    "CustomServerResult",
    "DnsCommandResult",
    "DnsState",
    "apply_dns",
    "consume_warmed_state",
    "delete_custom_server",
    "duplicate_custom_server",
    "flush_dns_cache",
    "is_isp_dns_warning_shown",
    "load_state",
    "mark_isp_dns_warning_shown",
    "measure_dns_latency",
    "migrate_outdated_dns_addresses",
    "repair_local_proxy",
    "reset_to_auto",
    "save_custom_server",
    "start_local_proxy",
    "stop_local_proxy_if_unused",
    "warm_state",
]
