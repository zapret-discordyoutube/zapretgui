"""Какие сетевые адаптеры показывать на странице DNS и в каком порядке.

Решение принимается по признакам, которые сообщает сама Windows, а не по
словам в названии:

- показываем настоящие сетевые карты — «аппаратный интерфейс» с «разъёмом»
  (Ethernet и Wi‑Fi; сетевая карта внутри виртуальной машины тоже такая);
- и адаптер Ethernet/Wi‑Fi, через который сейчас идёт интернет, даже если
  он виртуальный (Hyper‑V с внешним коммутатором: IP-настройки хоста живут
  на «vEthernet»);
- не показываем и не трогаем всё остальное: фильтры (WFP, QoS, Npcap),
  внутренние сети Hyper‑V/VMware/VirtualBox/WSL, VPN-туннели, Wi‑Fi Direct,
  отладчик ядра. Смена DNS на них ломает виртуалки и VPN.
"""

from __future__ import annotations

from dataclasses import dataclass

from dns.winapi import IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE80211, InternetRoute, RawInterface, StaticDns

DNS_ADAPTER_TYPES = (IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE80211)


@dataclass(frozen=True, slots=True)
class DnsAdapter:
    """Адаптер на странице DNS. Опознаётся по GUID: имя можно переименовать."""

    guid: str
    name: str
    description: str
    kind: str  # "ethernet" | "wifi"
    connected: bool
    internet: bool
    static_ipv4: tuple[str, ...] = ()
    static_ipv6: tuple[str, ...] = ()
    auto_ipv4: tuple[str, ...] = ()
    auto_ipv6: tuple[str, ...] = ()

    @property
    def is_automatic(self) -> bool:
        return not self.static_ipv4 and not self.static_ipv6


def is_internet_interface(interface: RawInterface, route: InternetRoute) -> bool:
    indexes = {index for index in (route.ipv4_index, route.ipv6_index) if index}
    return bool(indexes & {interface.index, interface.ipv6_index})


def is_dns_adapter(interface: RawInterface, route: InternetRoute) -> bool:
    if interface.filter or interface.if_type not in DNS_ADAPTER_TYPES:
        return False
    if interface.hardware and interface.connector:
        return True
    return is_internet_interface(interface, route)


def build_dns_adapters(
    interfaces: list[RawInterface],
    route: InternetRoute,
    static_dns: dict[str, StaticDns],
) -> tuple[DnsAdapter, ...]:
    """Адаптеры для страницы: сначала интернет, потом подключённые, потом по имени."""
    adapters: list[DnsAdapter] = []
    for interface in interfaces:
        if not is_dns_adapter(interface, route):
            continue
        static = static_dns.get(interface.guid, StaticDns())
        adapters.append(
            DnsAdapter(
                guid=interface.guid,
                name=interface.name,
                description=interface.description,
                kind="wifi" if interface.if_type == IF_TYPE_IEEE80211 else "ethernet",
                connected=interface.connected,
                internet=is_internet_interface(interface, route),
                static_ipv4=static.ipv4,
                static_ipv6=static.ipv6,
                auto_ipv4=() if static.ipv4 else interface.dns_ipv4,
                auto_ipv6=() if static.ipv6 else interface.dns_ipv6,
            )
        )
    adapters.sort(key=lambda item: (not item.internet, not item.connected, item.name.casefold()))
    return tuple(adapters)


__all__ = ["DnsAdapter", "build_dns_adapters", "is_dns_adapter", "is_internet_interface"]
