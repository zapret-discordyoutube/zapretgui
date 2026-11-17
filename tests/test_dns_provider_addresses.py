"""Адреса чужих серверов по имени DoH: узнаются сами — при нажатии плитки и раз в сутки."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dns import dns_providers, provider_addresses


def _found(*ipv4: str, error: str = ""):
    return SimpleNamespace(ipv4=tuple(ipv4), error=error)


class _Memory:
    """Раздел настроек dns.learned_servers в памяти вместо settings.sqlite3."""

    def __init__(self) -> None:
        self.value: dict = {}
        self.writes: list = []

    def stored(self) -> dict:
        return {name: dict(item) for name, item in self.value.items()}

    def save(self, name, *, ipv4, former_ipv4, checked_at) -> None:
        self.value[name] = {"ipv4": list(ipv4), "former_ipv4": list(former_ipv4), "checked_at": checked_at}
        self.writes.append(name)


class ProviderAddressesTests(unittest.TestCase):
    NEW = ("1.2.3.4", "5.6.7.8")

    def setUp(self) -> None:
        self.memory = _Memory()
        patches = [
            patch.object(provider_addresses, "_stored", self.memory.stored),
            patch.object(provider_addresses, "_save", self.memory.save),
            patch("hosts.proxy_domains.invalidate_hosts_catalog_cache"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        # adopt меняет список плитки на месте: вернуть встроенные адреса и память о них.
        self.malw = next(data for name, data in provider_addresses.tracked() if name == "Malw.link")
        shipped = list(self.malw["ipv4"])
        self.addCleanup(lambda: self.malw["ipv4"].__setitem__(slice(None), shipped))
        self.addCleanup(provider_addresses._SHIPPED.clear)
        provider_addresses._SHIPPED.clear()
        # Файл сборки читается раз за процесс: в тестах его нет, пока тест не положит свой.
        provider_addresses._BASELINE = {}
        self.addCleanup(setattr, provider_addresses, "_BASELINE", None)
        self.shipped = tuple(shipped)

    def _refresh(self, *ipv4: str, error: str = "", names=("Malw.link",)):
        return provider_addresses.refresh(names=names, lookup=lambda *_a, **_k: _found(*ipv4, error=error), now=100.0)

    def test_tracked_servers_are_the_ones_whose_addresses_come_from_their_doh_name(self) -> None:
        names = {name for name, _data in provider_addresses.tracked()}

        self.assertEqual(names, {"Malw.link", "AstraCat"})

    def test_new_addresses_are_stored_adopted_and_old_ones_become_former(self) -> None:
        (change,) = self._refresh(*self.NEW)

        self.assertTrue(change.changed)
        self.assertEqual(self.memory.value["Malw.link"]["ipv4"], list(self.NEW))
        self.assertEqual(set(self.memory.value["Malw.link"]["former_ipv4"]), set(self.shipped))
        # Плитка видит новые адреса сразу, адаптеры со старыми переводятся на первый новый.
        self.assertEqual(tuple(self.malw["ipv4"]), self.NEW)
        self.assertEqual(
            provider_addresses.address_replacements(self.memory.stored()),
            {address: self.NEW[0] for address in self.shipped},
        )

    def test_same_addresses_write_nothing(self) -> None:
        (change,) = self._refresh(*self.shipped)

        self.assertFalse(change.changed)
        self.assertEqual(self.memory.writes, [])

    def test_name_that_gives_nothing_leaves_the_known_addresses(self) -> None:
        (change,) = self._refresh(error="имя не находится")

        self.assertFalse(change.changed)
        self.assertEqual(change.ipv4, self.shipped)
        self.assertEqual(self.memory.writes, [])
        self.assertEqual(tuple(self.malw["ipv4"]), self.shipped)

    def test_failing_lookup_is_reported_not_raised(self) -> None:
        def broken(*_a, **_k):
            raise OSError("сеть")

        (change,) = provider_addresses.refresh(names=["Malw.link"], lookup=broken)

        self.assertFalse(change.changed)
        self.assertIn("не удался", change.detail)

    def test_only_global_ipv4_are_accepted(self) -> None:
        (change,) = self._refresh("10.0.0.1", "192.168.1.1", "127.0.0.1", "2001:db8::1", *self.NEW)

        self.assertEqual(change.ipv4, self.NEW)

    def test_apply_asks_the_name_right_now_and_returns_fresh_addresses(self) -> None:
        with patch("dns.doh_lookup.find_doh_addresses", lambda *_a, **_k: _found(*self.NEW)):
            fresh = provider_addresses.refresh_for_apply("Malw.link")

        self.assertEqual(fresh, self.NEW)
        self.assertEqual(self.memory.value["Malw.link"]["ipv4"], list(self.NEW))

    def test_apply_does_not_touch_servers_without_the_flag(self) -> None:
        with patch("dns.doh_lookup.find_doh_addresses", side_effect=AssertionError("в сеть не ходить")):
            self.assertEqual(provider_addresses.refresh_for_apply("Comss DNS"), ())

    def test_apply_falls_back_to_known_addresses_when_the_name_is_silent(self) -> None:
        with patch("dns.doh_lookup.find_doh_addresses", lambda *_a, **_k: _found(error="нет")):
            fresh = provider_addresses.refresh_for_apply("Malw.link")

        self.assertEqual(fresh, self.shipped)


class BuildBaselineTests(ProviderAddressesTests):
    """Адреса «на день сборки»: сборка узнаёт их по имени DoH и кладёт в файл, код править не нужно."""

    def test_build_learns_only_servers_whose_name_answered(self) -> None:
        def lookup(template, **_kwargs):
            return _found(*self.NEW) if "malw" in template.host else _found(error="закрыт из этой страны")

        result = provider_addresses.learn_baseline(lookup=lookup)

        self.assertEqual(result, {"Malw.link": list(self.NEW)})
        self.assertEqual(self.memory.writes, [], "сборка не пишет в настройки машины")

    def test_baseline_file_round_trips_and_ignores_junk(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "dns_provider_addresses.json"
            path.write_text(provider_addresses.baseline_json({"Malw.link": list(self.NEW)}), encoding="utf-8")
            self.assertEqual(provider_addresses.read_baseline(path), {"Malw.link": self.NEW})
            path.write_text(json.dumps({"A": ["10.0.0.1"], "B": "не список", "C": ["8.8.8.8"]}), encoding="utf-8")
            self.assertEqual(provider_addresses.read_baseline(path), {"C": ("8.8.8.8",)})
            path.write_text("{не json", encoding="utf-8")
            self.assertEqual(provider_addresses.read_baseline(path), {})
            self.assertEqual(provider_addresses.read_baseline(Path(folder) / "нет.json"), {})

    def test_baseline_beats_the_code_and_learned_beats_the_baseline(self) -> None:
        provider_addresses._BASELINE = {"Malw.link": self.NEW}

        self.assertEqual(provider_addresses.current_ipv4("Malw.link", self.malw, {}), self.NEW)
        learned = {"Malw.link": {"ipv4": ["8.8.8.8"], "former_ipv4": []}}
        self.assertEqual(provider_addresses.current_ipv4("Malw.link", self.malw, learned), ("8.8.8.8",))

    def test_adapters_on_a_code_address_move_to_the_baseline(self) -> None:
        provider_addresses._BASELINE = {"Malw.link": self.NEW}

        pairs = provider_addresses.address_replacements({})

        self.assertEqual(pairs, {address: self.NEW[0] for address in self.shipped})


class ApplyWorkerUsesFreshAddressesTests(unittest.TestCase):
    def test_apply_writes_the_address_found_at_the_moment_of_the_press(self) -> None:
        from dns.page_workers import DnsApplyWorker
        from dns.state import DnsCommandResult

        log: list = []

        def apply_dns(adapters, ipv4, ipv6):
            log.append(("apply", list(ipv4)))
            return DnsCommandResult(success=True, affected_count=len(adapters), total_count=len(adapters))

        data = {"ipv4": ["9.9.9.9"], "ipv6": [], "addresses_by_name": True, "doh": "https://example.test/dns-query"}
        worker = DnsApplyWorker(
            1,
            action="provider",
            adapters=["{guid}"],
            name="Malw.link",
            data=data,
            apply_dns=apply_dns,
            reset_to_auto=lambda adapters: None,
            load_state=lambda: "state",
            start_local_proxy=lambda *a, **k: None,
            stop_local_proxy_if_unused=lambda: None,
            lookup_doh_name=lambda _host: [],
            refresh_provider=lambda name: ("1.2.3.4", "5.6.7.8"),
        )

        worker._run_apply()

        self.assertEqual(log, [("apply", ["1.2.3.4", "5.6.7.8"])])

    def test_server_without_the_flag_is_not_asked(self) -> None:
        from dns.page_workers import DnsApplyWorker
        from dns.state import DnsCommandResult

        asked: list = []
        log: list = []

        def apply_dns(adapters, ipv4, ipv6):
            log.append(list(ipv4))
            return DnsCommandResult(success=True, affected_count=len(adapters), total_count=len(adapters))

        worker = DnsApplyWorker(
            1,
            action="provider",
            adapters=["{guid}"],
            name="Quad9",
            data={"ipv4": ["9.9.9.9"], "ipv6": []},
            apply_dns=apply_dns,
            reset_to_auto=lambda adapters: None,
            load_state=lambda: "state",
            start_local_proxy=lambda *a, **k: None,
            stop_local_proxy_if_unused=lambda: None,
            refresh_provider=lambda name: asked.append(name) or ("1.2.3.4",),
        )

        worker._run_apply()

        self.assertEqual(asked, [])
        self.assertEqual(log, [["9.9.9.9"]])


if __name__ == "__main__":
    unittest.main()
