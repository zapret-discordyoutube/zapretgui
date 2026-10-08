from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from dns import dns_providers
from dns.dns_providers import (
    DNS_PROVIDERS,
    STATUS_AT_RISK,
    STATUS_BLOCKED,
    catalog_problems,
    doh_templates,
    find_provider_by_address,
    is_encrypted_only,
    iter_providers,
    network_providers,
)
from dns.page_plans import find_provider_for_dns


class DnsProvidersCatalogTests(unittest.TestCase):
    """Правила списка DNS-серверов страницы «Настройка DNS»."""

    def test_catalog_has_no_problems(self) -> None:
        self.assertEqual(catalog_problems(), [])

    def test_broken_entries_are_reported(self) -> None:
        broken = copy.deepcopy(DNS_PROVIDERS)
        broken["Популярные"]["Копия"] = dict(broken["Популярные"]["Cloudflare"])
        broken["Популярные"]["Дубль"] = {"ipv4": ["9.9.9.1", "9.9.9.1"], "ipv6": ["нет"], "desc": "x", "icon": "fa5s.lock"}
        broken["Популярные"]["Пустой"] = {"ipv4": [], "desc": "", "icon": "", "doh": "http://x", "dot": "x/y", "status": "?"}

        with patch.object(dns_providers, "DNS_PROVIDERS", broken):
            problems = "\n".join(catalog_problems())

        self.assertIn("Копия: адрес 1.1.1.1 уже у сервера Cloudflare", problems)
        self.assertIn("Дубль: адрес IPv4 записан дважды", problems)
        self.assertIn("Дубль: «нет» — не адрес IPv6", problems)
        self.assertIn("Пустой: нет адреса IPv4", problems)
        self.assertIn("Пустой: адрес DoH должен начинаться с https://", problems)
        self.assertIn("Пустой: в «dot» нужно только имя сервера", problems)
        self.assertIn("Пустой: неизвестная пометка состояния", problems)
        self.assertIn("Пустой: нет пояснения или значка", problems)

    def test_every_server_is_recognized_by_its_primary_address(self) -> None:
        """По первому адресу на адаптере программа узнаёт именно этот сервер."""
        for _group, name, data in iter_providers():
            with self.subTest(provider=name):
                mode = data.get("local_proxy", "")
                self.assertEqual(find_provider_for_dns(DNS_PROVIDERS, data["ipv4"], data.get("ipv6", []), mode), name)

    def test_encrypted_modes_share_one_local_address_and_stay_out_of_network_lists(self) -> None:
        modes = {data["local_proxy"]: name for _group, name, data in iter_providers() if data.get("local_proxy")}

        self.assertEqual(set(modes), {"dnscrypt", "anonymized", "odoh"})
        self.assertEqual(list(DNS_PROVIDERS)[0], "Шифрованные")
        # Без работающего движка 127.0.0.1 — не наш режим.
        self.assertIsNone(find_provider_for_dns(DNS_PROVIDERS, ["127.0.0.1"], ["::1"]))
        self.assertEqual(find_provider_for_dns(DNS_PROVIDERS, [], ["::1"], "odoh"), "ODoH")
        self.assertNotIn("Шифрованные", network_providers())
        self.assertEqual(sum(len(group) for group in network_providers().values()) + 3, len(list(iter_providers())))
        self.assertIsNone(find_provider_by_address("127.0.0.1"))
        self.assertNotIn("127.0.0.1", doh_templates())

    def test_broken_encrypted_mode_is_reported(self) -> None:
        broken = copy.deepcopy(DNS_PROVIDERS)
        broken["Шифрованные"]["ODoH"]["local_proxy"] = "dnscrypt"
        broken["Шифрованные"]["DNSCrypt"]["ipv4"] = ["127.0.0.2"]
        broken["Популярные"]["Cloudflare"]["local_proxy"] = "odoh"

        with patch.object(dns_providers, "DNS_PROVIDERS", broken):
            problems = "\n".join(catalog_problems())

        self.assertIn("ODoH: неверный или повторный режим шифрованного DNS «dnscrypt»", problems)
        self.assertIn("DNSCrypt: у режима шифрованного DNS адреса должны быть 127.0.0.1 и ::1", problems)
        self.assertIn("Cloudflare: неверный или повторный режим", problems)

    def test_blocked_and_at_risk_marks(self) -> None:
        marks = {name: data.get("status", "") for _group, name, data in iter_providers()}

        self.assertEqual(marks["Cloudflare"], STATUS_BLOCKED)
        self.assertEqual(marks["Google DNS"], STATUS_BLOCKED)
        self.assertEqual(marks["AdGuard"], STATUS_AT_RISK)
        self.assertEqual(marks["OpenDNS"], STATUS_AT_RISK)
        self.assertEqual(marks["Quad9"], "")

    def test_servers_without_plain_dns_are_marked_and_must_have_doh(self) -> None:
        """Wikimedia и DNS-AI порт 53 не открывают: спросить их можно только шифрованным запросом."""
        marked = {name for _group, name, data in iter_providers() if is_encrypted_only(data)}

        self.assertEqual(marked, {"Wikimedia DNS", "DNS-AI"})
        self.assertIn("Wikimedia DNS", DNS_PROVIDERS["Малоизвестные"])
        self.assertIn("DNS-AI", DNS_PROVIDERS["Для ИИ"])

        broken = copy.deepcopy(DNS_PROVIDERS)
        del broken["Для ИИ"]["DNS-AI"]["doh"]
        with patch.object(dns_providers, "DNS_PROVIDERS", broken):
            self.assertIn("DNS-AI: сервер только с шифрованием должен иметь адрес DoH", catalog_problems())

    def test_dns_sb_is_not_among_popular_servers(self) -> None:
        self.assertNotIn("Dns.SB", DNS_PROVIDERS["Популярные"])
        self.assertIn("Dns.SB", DNS_PROVIDERS["Малоизвестные"])

    def test_server_is_found_by_any_of_its_addresses(self) -> None:
        self.assertEqual(find_provider_by_address("8.8.4.4")[:2], ("Популярные", "Google DNS"))
        self.assertEqual(find_provider_by_address("2620:FE::FE")[1], "Quad9")
        self.assertIsNone(find_provider_by_address("192.0.2.1"))
        self.assertIsNone(find_provider_by_address(""))

    def test_doh_template_covers_backup_and_ipv6_addresses(self) -> None:
        templates = doh_templates()

        self.assertEqual(templates["77.88.8.1"], "https://common.dot.dns.yandex.net/dns-query")
        self.assertEqual(templates["2a13:1001::86:54:11:200"], "https://unfiltered.joindns4.eu/dns-query")
        self.assertNotIn("84.200.69.80", templates)
        # Серверу только с шифрованием шаблон нужен на каждом адресе: без шаблона
        # Windows спросит адрес обычным DNS, а там никто не отвечает.
        for address in ("192.144.59.14", "186.246.49.127", "2a0d:8480:0:67c::14", "2a0a:2b41:0:500d::53"):
            self.assertEqual(templates[address], "https://dns.dns-ai.ru/dns-query")
        self.assertEqual(templates["2001:67c:930::1"], "https://wikimedia-dns.org/dns-query")

    def test_adguard_doh_uses_its_current_name(self) -> None:
        """Старое имя dns.adguard.com при проверке с Windows 2026-10-08 на DoH не отвечало."""
        templates = doh_templates()

        self.assertEqual(templates["94.140.14.14"], "https://dns.adguard-dns.com/dns-query")
        self.assertEqual(templates["2a10:50c0::ad2:ff"], "https://dns.adguard-dns.com/dns-query")


if __name__ == "__main__":
    unittest.main()
