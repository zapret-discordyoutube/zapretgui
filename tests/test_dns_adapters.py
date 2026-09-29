from __future__ import annotations

import unittest
from dataclasses import replace

from dns.adapters import build_dns_adapters, is_dns_adapter
from dns.winapi import IF_TYPE_ETHERNET_CSMACD as ETH, IF_TYPE_IEEE80211 as WIFI, InternetRoute, RawInterface, StaticDns


def _iface(index, name, *, if_type=ETH, hw=False, connector=False, filter=False, connected=True, dns=()):
    return RawInterface(
        guid=f"{{00000000-0000-0000-0000-{index:012d}}}",
        index=index,
        ipv6_index=index,
        name=name,
        description=name,
        if_type=if_type,
        connected=connected,
        hardware=hw,
        filter=filter,
        connector=connector,
        dns_ipv4=tuple(dns),
    )


# Как на реальном ПК со скриншота пользователя плюс типичные виртуальные адаптеры.
MACHINE = [
    _iface(9, "Ethernet", hw=True, connector=True, dns=("192.168.1.1",)),
    _iface(12, "Беспроводная сеть", if_type=WIFI, hw=True, connector=True, connected=False),
    _iface(15, "Ethernet-WFP Native MAC Layer LightWeight Filter-0000", filter=True),
    _iface(16, "Ethernet-Npcap Packet Driver (NPCAP)-0000", filter=True),
    _iface(4, "Ethernet (отладчик ядра)"),
    _iface(20, "Подключение по локальной сети* 9", if_type=WIFI, hw=True),  # Wi-Fi Direct: без разъёма
    _iface(30, "vEthernet (Default Switch)"),  # внутренняя сеть Hyper-V
    _iface(31, "VMware Network Adapter VMnet8"),
    _iface(32, "wintun", if_type=53),
]


class DnsAdapterSelectionTests(unittest.TestCase):
    def test_only_real_network_cards_are_shown(self) -> None:
        adapters = build_dns_adapters(MACHINE, InternetRoute(ipv4_index=9), {})

        self.assertEqual([adapter.name for adapter in adapters], ["Ethernet", "Беспроводная сеть"])
        self.assertTrue(adapters[0].internet)
        self.assertEqual(adapters[1].kind, "wifi")
        self.assertFalse(adapters[1].connected)

    def test_network_card_inside_virtual_machine_is_real(self) -> None:
        # Сетевая карта гостевой Windows (VirtIO, Hyper-V, VMware) помечена как аппаратная.
        guest = replace(_iface(9, "Ethernet", hw=True, connector=True), description="Microsoft Hyper-V Network Adapter")

        self.assertTrue(is_dns_adapter(guest, InternetRoute()))

    def test_hyper_v_external_switch_carrying_internet_is_shown(self) -> None:
        external = _iface(40, "vEthernet (External)")

        self.assertTrue(is_dns_adapter(external, InternetRoute(ipv4_index=40)))
        self.assertFalse(is_dns_adapter(external, InternetRoute(ipv4_index=9)))
        # Туннель VPN не трогаем, даже если через него идёт интернет.
        self.assertFalse(is_dns_adapter(MACHINE[-1], InternetRoute(ipv4_index=32)))
        self.assertFalse(is_dns_adapter(MACHINE[2], InternetRoute(ipv4_index=15)))

    def test_static_and_automatic_dns_are_separated(self) -> None:
        adapters = build_dns_adapters(
            MACHINE,
            InternetRoute(ipv4_index=9),
            {MACHINE[0].guid: StaticDns(ipv4=("1.1.1.1",)), MACHINE[1].guid: StaticDns()},
        )

        ethernet, wifi = adapters
        self.assertEqual(ethernet.static_ipv4, ("1.1.1.1",))
        self.assertEqual(ethernet.auto_ipv4, ())
        self.assertFalse(ethernet.is_automatic)
        self.assertTrue(wifi.is_automatic)

    def test_internet_adapter_first_then_connected(self) -> None:
        interfaces = [
            _iface(1, "B off", hw=True, connector=True, connected=False),
            _iface(2, "A on", hw=True, connector=True),
            _iface(3, "Z internet", hw=True, connector=True),
        ]

        adapters = build_dns_adapters(interfaces, InternetRoute(ipv4_index=3, ipv6_index=3), {})

        self.assertEqual([adapter.name for adapter in adapters], ["Z internet", "A on", "B off"])


if __name__ == "__main__":
    unittest.main()
