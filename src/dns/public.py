from __future__ import annotations

from dns.commands import (
    apply_dns,
    consume_warmed_state,
    flush_dns_cache,
    is_isp_dns_warning_shown,
    load_state,
    mark_isp_dns_warning_shown,
    measure_dns_latency,
    migrate_outdated_dns_addresses,
    reset_to_auto,
    warm_state,
)
from dns.dns_providers import DNS_PROVIDERS
from dns.state import DnsCommandResult, DnsState


__all__ = [
    "DNS_PROVIDERS",
    "DnsCommandResult",
    "DnsState",
    "apply_dns",
    "consume_warmed_state",
    "flush_dns_cache",
    "is_isp_dns_warning_shown",
    "load_state",
    "mark_isp_dns_warning_shown",
    "measure_dns_latency",
    "migrate_outdated_dns_addresses",
    "reset_to_auto",
    "warm_state",
]
