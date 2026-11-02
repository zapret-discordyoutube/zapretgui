"""Свой сервер Zapret DNS: адрес в одном месте, адрес по имени, замена на адаптерах, отметка."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import zapret_dns
from dns import dns_providers, zapret_dns_server
from dns.address_migration import outdated_address_plan, plan_dns_server_migration
from settings import store

NEW = "198.51.100.7"  # документационный адрес не годится: он не глобальный
GLOBAL_NEW = "1.2.3.4"


class _Settings:
    """Раздел настроек dns.zapret_dns_server в памяти вместо settings.sqlite3."""

    def __init__(self, value: dict | None = None) -> None:
        self.value = dict(value or {"ipv4": [], "former_ipv4": [], "checked_at": 0.0})
        self.writes: list[dict] = []

    def get(self) -> dict:
        return dict(self.value)

    def set(self, *, ipv4, former_ipv4, checked_at) -> None:
        self.value = {"ipv4": list(ipv4), "former_ipv4": list(former_ipv4), "checked_at": checked_at}
        self.writes.append(dict(self.value))

    def patch(self):
        return patch.multiple(store, get_zapret_dns_server=self.get, set_zapret_dns_server=self.set)


class ZapretDnsServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tile = list(dns_providers.zapret_dns_provider()["ipv4"])
        self._active = zapret_dns_server._ACTIVE_IPV4
        invalidate = patch("hosts.proxy_domains.invalidate_hosts_catalog_cache")
        self.invalidate = invalidate.start()
        self.addCleanup(invalidate.stop)

    def tearDown(self) -> None:
        dns_providers.zapret_dns_provider()["ipv4"][:] = self._tile
        zapret_dns_server._ACTIVE_IPV4 = self._active

    def test_the_tile_takes_everything_from_the_one_definition(self) -> None:
        entry = dns_providers.zapret_dns_provider()
        self.assertEqual(tuple(entry["ipv4"]), zapret_dns.IPV4)
        self.assertEqual(tuple(entry["ipv6"]), zapret_dns.IPV6)
        self.assertEqual((entry["doh"], entry["dot"]), (zapret_dns.DOH_URL, zapret_dns.DOT_HOST))

    def test_without_a_learned_address_the_built_in_one_is_current(self) -> None:
        settings = _Settings()
        with settings.patch():
            self.assertEqual(zapret_dns_server.current_ipv4(), zapret_dns.IPV4)
            self.assertEqual(zapret_dns_server.former_ipv4(), zapret_dns.FORMER_IPV4)
            pairs = zapret_dns_server.address_replacements()
        self.assertEqual(pairs, {old: zapret_dns.IPV4[0] for old in zapret_dns.FORMER_IPV4})

    def test_a_learned_address_moves_everyone_off_the_built_in_one(self) -> None:
        settings = _Settings({"ipv4": [GLOBAL_NEW], "former_ipv4": [], "checked_at": 1.0})
        with settings.patch():
            self.assertEqual(zapret_dns_server.current_ipv4(), (GLOBAL_NEW,))
            pairs = zapret_dns_server.address_replacements()
            self.assertEqual(zapret_dns_server.adopt(), (GLOBAL_NEW,))
        self.assertEqual(pairs[zapret_dns.IPV4[0]], GLOBAL_NEW)
        self.assertEqual(pairs["83.217.211.149"], GLOBAL_NEW)
        self.assertEqual(dns_providers.zapret_dns_provider()["ipv4"], [GLOBAL_NEW])
        self.assertEqual(zapret_dns_server.active_ipv4(), (GLOBAL_NEW,))
        self.invalidate.assert_called_once()

    def test_a_stored_non_global_address_is_never_used(self) -> None:
        settings = _Settings({"ipv4": [NEW, "10.0.0.1", "garbage"], "former_ipv4": [], "checked_at": 1.0})
        with settings.patch():
            self.assertEqual(zapret_dns_server.current_ipv4(), zapret_dns.IPV4)

    def test_refresh_adopts_only_a_verified_new_address(self) -> None:
        settings = _Settings()
        found = SimpleNamespace(ipv4=(GLOBAL_NEW,), error="")
        asked: list = []

        def lookup(template, **kwargs):
            asked.append((template.host, kwargs))
            return found

        with settings.patch():
            change = zapret_dns_server.refresh(lookup=lookup, now=5.0)
        self.assertTrue(change.changed)
        self.assertEqual(asked[0][0], zapret_dns.HOST)
        self.assertFalse(asked[0][1]["ipv6"])
        self.assertEqual(settings.value["ipv4"], [GLOBAL_NEW])
        # Встроенный адрес становится прежним: с него переведут адаптеры.
        self.assertIn(zapret_dns.IPV4[0], settings.value["former_ipv4"])
        self.assertEqual(dns_providers.zapret_dns_provider()["ipv4"], [GLOBAL_NEW])

    def test_a_failed_question_never_changes_the_server(self) -> None:
        settings = _Settings()
        with settings.patch():
            change = zapret_dns_server.refresh(lookup=lambda *_a, **_k: SimpleNamespace(ipv4=(), error="молчит"))
        self.assertFalse(change.changed)
        self.assertEqual(change.detail, "молчит")
        self.assertEqual(settings.writes, [])
        self.assertEqual(dns_providers.zapret_dns_provider()["ipv4"], list(zapret_dns.IPV4))

    def test_the_same_address_only_records_the_check(self) -> None:
        settings = _Settings()
        with settings.patch():
            change = zapret_dns_server.refresh(lookup=lambda *_a, **_k: SimpleNamespace(ipv4=zapret_dns.IPV4), now=9.0)
        self.assertFalse(change.changed)
        self.assertEqual(settings.writes[-1]["checked_at"], 9.0)
        self.invalidate.assert_not_called()

    def test_check_in_asks_the_current_server_one_plain_question(self) -> None:
        asked: list = []

        def query(server, name, rtype, **kwargs):
            asked.append((server, name))
            return SimpleNamespace(answered=True)

        self.assertTrue(zapret_dns_server.check_in(query=query))
        self.assertEqual(asked, [(zapret_dns.IPV4[0], f"hello.check.{zapret_dns.HOST}")])


class AddressMigrationTests(unittest.TestCase):
    def test_former_ipv6_without_a_successor_is_removed(self) -> None:
        self.assertEqual(zapret_dns.IPV6, ())
        replacements, removals = outdated_address_plan()
        self.assertEqual(set(removals), set(zapret_dns.FORMER_IPV6))
        self.assertIsNone(plan_dns_server_migration(["2001:4860:4860::8888"], replacements, removals))
        self.assertEqual(plan_dns_server_migration(["2A03:AFC0:9:0:0:0:0:235D"], replacements, removals), [])
        self.assertEqual(
            plan_dns_server_migration(["2a03:afc0:9::235d", "2606:4700:4700::1111"], replacements, removals),
            ["2606:4700:4700::1111"],
        )

    def test_every_former_ipv4_leads_to_the_current_server(self) -> None:
        replacements, _removals = outdated_address_plan()
        for former in zapret_dns.FORMER_IPV4:
            self.assertEqual(
                plan_dns_server_migration([former, "1.1.1.1"], replacements), [zapret_dns_server.current_ipv4()[0], "1.1.1.1"]
            )
        # Сторонние замены (Xbox → DNS-AI) остаются в общем плане.
        self.assertIn("111.88.96.54", replacements)


class CatalogMarkerTests(unittest.TestCase):
    def test_the_marker_becomes_the_active_address_only_in_its_profile(self) -> None:
        from hosts import catalog_repository

        marker = zapret_dns.CATALOG_ADDRESS_MARKER
        self.assertEqual(catalog_repository._answer_address("zapret_dns", marker), zapret_dns_server.active_ipv4()[0])
        self.assertEqual(catalog_repository._answer_address("geohide", "193.233.112.67"), "193.233.112.67")
        with self.assertRaises(catalog_repository.HostsCatalogError):
            catalog_repository._answer_address("geohide", marker)

    def test_the_shipped_catalog_carries_no_server_address(self) -> None:
        database = Path(__file__).resolve().parents[2] / "private_zapretgui/resources/system/hosts_catalog.sqlite3"
        if not database.is_file():
            self.skipTest("нет закрытой части проекта")
        with tempfile.TemporaryDirectory():
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                addresses = {row[0] for row in connection.execute("SELECT DISTINCT ip_address FROM dns_answers")}
            finally:
                connection.close()
        self.assertFalse(addresses & set(zapret_dns.IPV4 + zapret_dns.FORMER_IPV4))
        self.assertIn(zapret_dns.CATALOG_ADDRESS_MARKER, addresses)


class HostsRefreshGuardTests(unittest.TestCase):
    def test_an_unloaded_catalog_never_wipes_the_written_block(self) -> None:
        from hosts import commands

        manager = SimpleNamespace(refresh_applied_service_selection=lambda *a, **k: self.fail("блок переписан"))
        with patch("hosts.proxy_domains.get_all_services", return_value=[]):
            result = commands.refresh_applied_selection(manager)
        self.assertFalse(result.success)
        self.assertFalse(result.changed)
        self.assertIn("не трогается", result.message)


class SettingsNormalizeTests(unittest.TestCase):
    def test_only_global_ipv4_and_a_sane_time_are_kept(self) -> None:
        from settings.normalize import normalize_dns

        value = normalize_dns(
            {"zapret_dns_server": {"ipv4": ["1.2.3.4", "10.0.0.1", "::1", "x", "1.2.3.4"], "checked_at": "bad"}}
        )["zapret_dns_server"]
        self.assertEqual(value, {"ipv4": ["1.2.3.4"], "former_ipv4": [], "checked_at": 0.0})


if __name__ == "__main__":
    unittest.main()
