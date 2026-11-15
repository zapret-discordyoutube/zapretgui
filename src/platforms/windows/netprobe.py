from __future__ import annotations

import os
from collections.abc import Callable

from platforms.netprobe_types import DnsAnswer, PingResult, TraceHopResult


class WindowsNetProbe:
    """Network measurements through Windows APIs (ICMP, dnsapi, SIO_TCP_INFO).

    The modules are imported inside the methods: each one prepares its DLL tables at import,
    and a person who never opens the diagnostics should not pay for that at start.
    """

    def ping_ipv4_host(
        self,
        host: str,
        *,
        count: int,
        timeout_ms: int,
        resolved_ip: str | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PingResult:
        from platforms.windows.icmp import ping_ipv4_host_winapi

        return ping_ipv4_host_winapi(
            host, count=count, timeout_ms=timeout_ms, resolved_ip=resolved_ip, cancelled=cancelled
        )

    def ping_ipv6(
        self, ip: str, *, count: int, timeout_ms: int, cancelled: Callable[[], bool] | None = None
    ) -> PingResult:
        from platforms.windows.icmp import ping_ipv6_winapi

        return ping_ipv6_winapi(ip, count=count, timeout_ms=timeout_ms, cancelled=cancelled)

    def trace_hop_ipv4(self, ip: str, ttl: int, *, timeout_ms: int) -> TraceHopResult:
        from platforms.windows.icmp import trace_hop_ipv4

        return trace_hop_ipv4(ip, ttl, timeout_ms=timeout_ms)

    def query_ipv4(
        self, name: str, *, timeout: float, cancelled: Callable[[], bool] | None = None
    ) -> DnsAnswer:
        from platforms.windows.dns_query import query_ipv4

        return query_ipv4(name, timeout=timeout, cancelled=cancelled)

    def system_dns_servers(self) -> tuple[str, ...]:
        from platforms.windows.dns_query import system_dns_servers

        return system_dns_servers()

    def tcp_server_window(self, ip: str, *, port: int = 443, timeout: float = 4.0) -> int | None:
        from platforms.windows.tcp_info import server_window

        return server_window(ip, port=port, timeout=timeout)

    def hosts_file_path(self) -> str:
        root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
        return os.path.join(root, "System32", "drivers", "etc", "hosts")
