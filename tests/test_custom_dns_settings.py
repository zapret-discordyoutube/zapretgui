from __future__ import annotations

import unittest

from dns.custom_servers import CUSTOM_DNS_CATEGORY, build_dns_providers_with_custom
from settings import schema
from settings.normalize import normalize_dns


class CustomDnsSettingsTests(unittest.TestCase):
    def test_default_dns_settings_include_empty_custom_servers_list(self) -> None:
        self.assertEqual(schema.default_dns()["custom_servers"], [])

    def test_custom_dns_settings_are_normalized(self) -> None:
        normalized = normalize_dns(
            {
                "custom_servers": [
                    {
                        "id": "home",
                        "name": " Домашний DNS ",
                        "ipv4": ["8.8.8.8", "8.8.8.8", "bad"],
                        "ipv6": ["2001:4860:4860::8888"],
                    },
                    {
                        "id": "empty",
                        "name": "Пустой",
                        "ipv4": [],
                    },
                ]
            }
        )

        self.assertEqual(
            normalized["custom_servers"],
            [
                {
                    "id": "home",
                    "name": "Домашний DNS",
                    "ipv4": ["8.8.8.8"],
                    "ipv6": ["2001:4860:4860::8888"],
                    "doh": "",
                }
            ],
        )

    def test_doh_address_is_kept_only_when_it_is_https(self) -> None:
        def doh_of(value):
            servers = normalize_dns({"custom_servers": [{"id": "a", "name": "A", "ipv4": ["9.9.9.9"], "doh": value}]})
            return servers["custom_servers"][0]["doh"]

        self.assertEqual(doh_of(" https://dns.example.com/dns-query "), "https://dns.example.com/dns-query")
        self.assertEqual(doh_of("http://dns.example.com/dns-query"), "")
        self.assertEqual(doh_of("https://dns.example.com/dns query"), "")
        self.assertEqual(doh_of(None), "")

    def test_server_with_doh_but_without_addresses_is_dropped(self) -> None:
        # Windows принимает только IP-адреса: запись без них применить нельзя.
        normalized = normalize_dns(
            {"custom_servers": [{"id": "a", "name": "A", "doh": "https://dns.example.com/dns-query"}]}
        )

        self.assertEqual(normalized["custom_servers"], [])

    def test_custom_dns_settings_become_provider_group(self) -> None:
        providers = build_dns_providers_with_custom(
            {"Основные": {"Cloudflare": {"ipv4": ["1.1.1.1"], "ipv6": []}}},
            [
                {
                    "id": "home",
                    "name": "Домашний DNS",
                    "ipv4": ["8.8.8.8"],
                    "ipv6": [],
                }
            ],
        )

        self.assertIn(CUSTOM_DNS_CATEGORY, providers)
        self.assertEqual(providers[CUSTOM_DNS_CATEGORY]["Домашний DNS"]["ipv4"], ["8.8.8.8"])
        self.assertIn("Cloudflare", providers["Основные"])


if __name__ == "__main__":
    unittest.main()
