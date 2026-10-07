from __future__ import annotations

from dataclasses import dataclass

from dns.adapters import DnsAdapter


@dataclass(frozen=True, slots=True)
class DnsState:
    """Снимок DNS-слоя для страницы: адаптеры с их DNS, есть ли IPv6 и DoH."""

    adapters: tuple[DnsAdapter, ...] = ()
    ipv6_available: bool = False
    doh_supported: bool = False
    # Режим работающего шифрованного DNS (dns.local_proxy) или пустая строка.
    local_proxy_mode: str = ""


@dataclass(frozen=True, slots=True)
class DnsCommandResult:
    success: bool
    message: str = ""
    affected_count: int = 0
    total_count: int = 0
    # Движок шифрованного DNS слушает ещё и ::1 (ответ start_local_proxy).
    listen_ipv6: bool = False
