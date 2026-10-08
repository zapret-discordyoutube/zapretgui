from __future__ import annotations

import unittest
from unittest.mock import patch

from dns import runtime, winapi
from dns.adapters import DnsAdapter
from dns.address_migration import migrate_outdated_dns_addresses
from dns.dns_providers import DNS_PROVIDERS, doh_templates
from dns.state import DnsState

ETH = "{00000000-0000-0000-0000-000000000009}"
WIFI = "{00000000-0000-0000-0000-000000000012}"


class _FakeWinApi:
    def __init__(self, *, doh: bool = False, fail: set[str] = frozenset()) -> None:
        self.doh = doh
        self.fail = set(fail)
        self.writes: list[tuple[str, tuple[str, ...], bool, object]] = []
        self.flushes = 0

    def write_dns(self, guid, servers, *, ipv6, doh_templates=None):
        if guid in self.fail:
            raise winapi.DnsWinApiError("SetInterfaceDnsSettings: ошибка Windows 5 — Отказано в доступе.")
        self.writes.append((guid, tuple(servers), ipv6, doh_templates))

    def flush_resolver_cache(self):
        self.flushes += 1
        return True

    def is_doh_supported(self):
        return self.doh

    def patches(self):
        return [
            patch.object(winapi, name, getattr(self, name))
            for name in ("write_dns", "flush_resolver_cache", "is_doh_supported")
        ]


KNOWN = DnsState(
    adapters=(
        DnsAdapter(ETH, "Ethernet", "", "ethernet", True, True),
        DnsAdapter(WIFI, "Беспроводная сеть", "", "wifi", True, False),
    )
)


class DnsRuntimeTests(unittest.TestCase):
    def _run(self, fake: _FakeWinApi, func, *args, custom_servers=()):
        patchers = [
            *fake.patches(),
            patch.object(runtime, "load_state", return_value=KNOWN),
            patch.object(runtime, "load_custom_servers", return_value=list(custom_servers)),
        ]
        for item in patchers:
            item.start()
            self.addCleanup(item.stop)
        return func(*args)

    def test_apply_writes_both_families_then_flushes_cache(self) -> None:
        fake = _FakeWinApi()

        result = self._run(fake, runtime.apply_dns, [ETH, WIFI, ETH], ["1.1.1.1", "1.0.0.1"], [])

        self.assertEqual(
            fake.writes,
            [
                (ETH, ("1.1.1.1", "1.0.0.1"), False, None),
                (ETH, (), True, None),
                (WIFI, ("1.1.1.1", "1.0.0.1"), False, None),
                (WIFI, (), True, None),
            ],
        )
        self.assertEqual(fake.flushes, 1)
        self.assertTrue(result.success)
        self.assertEqual((result.affected_count, result.total_count), (2, 2))

    def test_windows_11_gets_doh_templates_for_known_servers(self) -> None:
        fake = _FakeWinApi(doh=True)

        self._run(fake, runtime.reset_to_auto, [ETH])

        self.assertEqual([write[1] for write in fake.writes], [(), ()])
        self.assertEqual(fake.writes[0][3], doh_templates())

    def test_custom_server_addresses_get_their_own_doh_template(self) -> None:
        fake = _FakeWinApi(doh=True)
        custom = [
            {"id": "a", "name": "Мой", "ipv4": ["203.0.113.5"], "ipv6": ["2001:db8::5"], "doh": "https://dns.example.com/dns-query"},
            {"id": "b", "name": "Без шифрования", "ipv4": ["203.0.113.9"], "ipv6": [], "doh": ""},
            # Свой сервер с адресом сервера программы шаблон программы не перебивает.
            {"id": "c", "name": "Подмена", "ipv4": ["1.1.1.1"], "ipv6": [], "doh": "https://other.example/dns-query"},
        ]

        self._run(fake, runtime.apply_dns, [ETH], ["203.0.113.5"], ["2001:db8::5"], custom_servers=custom)

        templates = fake.writes[0][3]
        self.assertEqual(templates["203.0.113.5"], "https://dns.example.com/dns-query")
        self.assertEqual(templates["2001:db8::5"], "https://dns.example.com/dns-query")
        self.assertNotIn("203.0.113.9", templates)
        self.assertEqual(templates["1.1.1.1"], doh_templates()["1.1.1.1"])
        self.assertEqual(fake.writes[1][3], templates)

    def test_windows_without_doh_gets_no_templates_even_for_custom_servers(self) -> None:
        fake = _FakeWinApi(doh=False)
        custom = [{"id": "a", "name": "Мой", "ipv4": ["203.0.113.5"], "ipv6": [], "doh": "https://dns.example.com/dns-query"}]

        self._run(fake, runtime.apply_dns, [ETH], ["203.0.113.5"], [], custom_servers=custom)

        self.assertIsNone(fake.writes[0][3])

    def test_state_carries_custom_servers_from_settings(self) -> None:
        custom = [{"id": "a", "name": "Мой", "ipv4": ["203.0.113.5"], "ipv6": [], "doh": ""}]
        route = type("Route", (), {"has_ipv6": False})()
        with patch.object(winapi, "list_interfaces", return_value=[]), patch.object(
            winapi, "internet_route", return_value=route
        ), patch.object(winapi, "is_doh_supported", return_value=True), patch.object(
            runtime, "_local_proxy_mode", return_value=""
        ), patch("settings.store.get_custom_dns_servers", return_value=custom):
            state = runtime.load_state()

        self.assertEqual(state.custom_servers, tuple(custom))

    def test_unreadable_settings_give_no_custom_servers(self) -> None:
        with patch("settings.store.get_custom_dns_servers", side_effect=RuntimeError("база занята")), patch.object(
            runtime, "log"
        ):
            self.assertEqual(runtime.load_custom_servers(), [])

    def test_failed_adapter_is_reported_by_name(self) -> None:
        fake = _FakeWinApi(fail={WIFI})

        result = self._run(fake, runtime.apply_dns, [ETH, WIFI], ["9.9.9.9"], ["2620:fe::fe"])

        self.assertFalse(result.success)
        self.assertEqual((result.affected_count, result.total_count), (1, 2))
        self.assertIn("«Беспроводная сеть»: SetInterfaceDnsSettings: ошибка Windows 5", result.message)

    def test_unknown_adapter_is_never_written(self) -> None:
        # Windows молча принимает любой GUID и заводит под него ветку реестра.
        fake = _FakeWinApi()
        ghost = "{00000000-0000-0000-0000-000000000000}"

        result = self._run(fake, runtime.apply_dns, [ghost], ["1.1.1.1"], [])

        self.assertEqual(fake.writes, [])
        self.assertEqual(fake.flushes, 0)
        self.assertFalse(result.success)
        self.assertIn("не найден", result.message)

    def test_warmed_state_is_given_out_once(self) -> None:
        state = DnsState()
        with patch.object(runtime, "load_state", return_value=state):
            runtime.warm_state()

        self.assertIs(runtime.consume_warmed_state(), state)
        self.assertIsNone(runtime.consume_warmed_state())

    def test_migration_replaces_only_outdated_static_addresses(self) -> None:
        fake = _FakeWinApi()
        adapters = (
            DnsAdapter(ETH, "Ethernet", "", "ethernet", True, True, static_ipv4=("176.99.11.77", "80.78.247.254"), static_ipv6=("2a00:ab00:1233:26::50",)),
            DnsAdapter(WIFI, "Wi-Fi", "", "wifi", True, False, static_ipv4=("1.1.1.1",)),
        )

        with patch.object(runtime, "adapters_with_static_dns", return_value=adapters):
            changes = self._run(fake, migrate_outdated_dns_addresses)

        self.assertEqual(
            fake.writes,
            # Адреса IPv6 действующего сервера в заменах нет: их не трогают.
            [(ETH, ("111.88.96.54", "111.88.96.55"), False, None)],
        )
        self.assertEqual(len(changes), 1)
        self.assertEqual(fake.flushes, 1)


class DnsProviderCatalogTests(unittest.TestCase):
    def test_every_provider_with_doh_has_templates_for_all_addresses(self) -> None:
        templates = doh_templates()
        for group in DNS_PROVIDERS.values():
            for name, data in group.items():
                for address in (*data["ipv4"], *data["ipv6"]):
                    with self.subTest(provider=name, address=address):
                        if data.get("doh"):
                            self.assertEqual(templates[address], data["doh"])
                        else:
                            # У сервера без DoH шаблона нет: Windows не шифрует запросы к нему.
                            self.assertNotIn(address, templates)

    def test_hosts_dns_services_are_available_as_dns_servers(self) -> None:
        ai = DNS_PROVIDERS["Для ИИ"]

        self.assertEqual(ai["Xbox DNS"]["ipv4"], ["111.88.96.54", "111.88.96.55"])
        self.assertEqual(ai["AstraCat"]["ipv4"], ["135.106.217.200", "135.106.197.22"])
        self.assertEqual(ai["GeoHide"]["ipv4"], ["193.233.112.67", "193.233.112.68"])
        # Серверы, которые перестали отвечать, из списка убраны.
        self.assertNotIn("dns.malw.link", ai)
        self.assertNotIn("Xbox DNS (old)", ai)


if __name__ == "__main__":
    unittest.main()
