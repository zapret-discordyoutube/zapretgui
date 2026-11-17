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
        # Какие адреса «отвечают»: (DoH, обычный DNS). По умолчанию встроенные живы целиком.
        self.alive: dict[str, tuple[bool, bool]] = {}
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
        self.addCleanup(provider_addresses._CHECKED.clear)
        provider_addresses._CHECKED.clear()
        # Файл сборки читается раз за процесс: в тестах его нет, пока тест не положит свой.
        provider_addresses._BASELINE = {}
        self.addCleanup(setattr, provider_addresses, "_BASELINE", None)
        self.shipped = tuple(shipped)
        for address in shipped:
            self.alive[address] = (True, True)

    def _verify(self, address, template, *, encrypted_only, cancel):
        return self.alive.get(address, (False, False))

    def _refresh(self, *ipv4: str, error: str = "", names=("Malw.link",)):
        return provider_addresses.refresh(
            names=names, lookup=lambda *_a, **_k: _found(*ipv4, error=error), verify=self._verify, now=100.0
        )

    def test_every_server_with_a_doh_name_is_tracked_except_the_engine_modes_and_own_server(self) -> None:
        names = {name for name, _data in provider_addresses.tracked()}

        self.assertTrue({"Malw.link", "AstraCat", "GeoHide", "GeoHide EU", "Comss DNS", "DNS-AI", "Cloudflare"} <= names, names)
        self.assertNotIn("Zapret DNS", names)
        self.assertNotIn("НСДИ", names)
        for _name, data in provider_addresses.tracked():
            self.assertTrue(data.get("doh") and data.get("ipv4"))
            self.assertFalse(data.get("local_proxy"))

    def test_a_new_verified_address_is_added_and_dead_old_ones_are_dropped(self) -> None:
        for address in self.NEW:
            self.alive[address] = (True, True)
        for address in self.shipped:
            self.alive[address] = (False, False)

        (change,) = self._refresh(*self.NEW)

        self.assertTrue(change.changed)
        self.assertEqual(self.memory.value["Malw.link"]["ipv4"], list(self.NEW))
        self.assertEqual(set(self.memory.value["Malw.link"]["former_ipv4"]), set(self.shipped))
        # Плитка видит новые адреса сразу, адаптеры с ушедшими переводятся на первый новый.
        self.assertEqual(tuple(self.malw["ipv4"]), self.NEW)
        self.assertEqual(
            provider_addresses.address_replacements(self.memory.stored()),
            {address: self.NEW[0] for address in self.shipped},
        )

    def test_a_living_built_in_address_stays_first_and_the_new_one_follows(self) -> None:
        self.alive[self.NEW[0]] = (True, True)

        (change,) = self._refresh(self.NEW[0])

        self.assertEqual(change.ipv4, (*self.shipped, self.NEW[0]))
        self.assertEqual(provider_addresses.address_replacements(self.memory.stored()), {})

    def test_address_that_only_answers_plain_dns_stays_but_goes_after_the_doh_ones(self) -> None:
        # GeoHide: встроенный адрес отвечает обычному DNS, а DoH живёт только по адресу имени.
        self.alive[self.shipped[0]] = (False, True)
        self.alive[self.shipped[1]] = (False, False)
        self.alive[self.NEW[0]] = (True, True)

        (change,) = self._refresh(self.NEW[0])

        self.assertEqual(change.ipv4, (self.NEW[0], self.shipped[0]))

    def test_same_addresses_write_nothing(self) -> None:
        (change,) = self._refresh(*self.shipped)

        self.assertFalse(change.changed)
        self.assertEqual(self.memory.writes, [])

    def test_name_that_gives_nothing_still_drops_a_dead_address_but_keeps_the_rest(self) -> None:
        self.alive[self.shipped[1]] = (False, False)

        (change,) = self._refresh(error="имя не находится")

        self.assertEqual(change.ipv4, (self.shipped[0],))

    def test_nothing_answers_means_nothing_changes(self) -> None:
        # Нет сети: ни один адрес не прошёл проверку — сервер остаётся как был.
        self.alive.clear()

        (change,) = self._refresh(error="имя не находится")

        self.assertFalse(change.changed)
        self.assertEqual(change.ipv4, self.shipped)
        self.assertEqual(self.memory.writes, [])
        self.assertEqual(tuple(self.malw["ipv4"]), self.shipped)

    def test_failing_lookup_is_reported_not_raised(self) -> None:
        def broken(*_a, **_k):
            raise OSError("сеть")

        self.alive.clear()
        (change,) = provider_addresses.refresh(names=["Malw.link"], lookup=broken, verify=self._verify)

        self.assertFalse(change.changed)
        self.assertIn("не удался", change.detail)

    def test_only_global_ipv4_are_accepted(self) -> None:
        for address in self.NEW:
            self.alive[address] = (True, True)

        (change,) = self._refresh("10.0.0.1", "192.168.1.1", "127.0.0.1", "2001:db8::1", *self.NEW)

        self.assertEqual(change.ipv4, (*self.shipped, *self.NEW))

    def test_encrypted_only_server_is_judged_by_doh_alone(self) -> None:
        data = {"ipv4": ["9.9.9.1", "9.9.9.2"], "doh": "https://x.test/dns-query", "encrypted_only": True}
        asked: list = []

        def verify(address, template, *, encrypted_only, cancel):
            asked.append(encrypted_only)
            return (address == "9.9.9.2", False)

        with patch.object(provider_addresses, "tracked", lambda: [("Тест", data)]):
            (change,) = provider_addresses.refresh(
                lookup=lambda *_a, **_k: _found(), verify=verify, now=1.0
            )

        self.assertEqual(change.ipv4, ("9.9.9.2",))
        self.assertTrue(all(asked))

    def test_name_addresses_of_a_shared_name_are_not_added_while_the_known_ones_live(self) -> None:
        # CleanBrowsing: одно имя DoH обслуживает все фильтры, адрес имени — не тот фильтр, что у плитки.
        data = {"ipv4": ["9.9.9.1", "9.9.9.2"], "doh": "https://x.test/dns-query"}
        alive = {"9.9.9.1": (True, True), "9.9.9.2": (True, True), "8.8.1.1": (True, True)}

        def verify(address, template, *, encrypted_only, cancel):
            return alive.get(address, (False, False))

        with patch.object(provider_addresses, "tracked", lambda: [("Тест", data)]):
            (change,) = provider_addresses.refresh(lookup=lambda *_a, **_k: _found("8.8.1.1"), verify=verify, now=1.0)
            self.assertEqual((change.changed, change.ipv4), (False, ("9.9.9.1", "9.9.9.2")))
            # Все известные адреса мертвы: тогда имя — единственный источник, и ему верят.
            alive["9.9.9.1"] = alive["9.9.9.2"] = (False, False)
            (change,) = provider_addresses.refresh(lookup=lambda *_a, **_k: _found("8.8.1.1"), verify=verify, now=1.0)
            self.assertEqual(change.ipv4, ("8.8.1.1",))

    def test_the_ai_servers_are_trusted_with_the_addresses_of_their_name(self) -> None:
        flagged = {name for name, data in provider_addresses.tracked() if data.get("addresses_by_name")}

        self.assertEqual(
            flagged,
            {"Comss DNS", "AstraCat", "GeoHide", "GeoHide EU", "GeoHide US", "Malw.link", "Bezmezhau", "DNS-AI"},
        )

    def test_apply_checks_right_now_and_returns_fresh_addresses(self) -> None:
        for address in self.NEW:
            self.alive[address] = (True, True)
        for address in self.shipped:
            self.alive[address] = (False, False)

        with patch("dns.doh_lookup.find_doh_addresses", lambda *_a, **_k: _found(*self.NEW)):
            fresh = provider_addresses.refresh_for_apply("Malw.link", verify=self._verify)

        self.assertEqual(fresh, self.NEW)
        self.assertEqual(self.memory.value["Malw.link"]["ipv4"], list(self.NEW))

    def test_apply_does_not_repeat_a_check_made_a_moment_ago(self) -> None:
        with patch("dns.doh_lookup.find_doh_addresses", lambda *_a, **_k: _found(*self.shipped)):
            provider_addresses.refresh_for_apply("Malw.link", verify=self._verify)
        with patch("dns.doh_lookup.find_doh_addresses", side_effect=AssertionError("в сеть не ходить")):
            fresh = provider_addresses.refresh_for_apply("Malw.link", verify=self._verify)

        self.assertEqual(fresh, self.shipped)

    def test_apply_does_not_touch_servers_without_doh(self) -> None:
        with patch("dns.doh_lookup.find_doh_addresses", side_effect=AssertionError("в сеть не ходить")):
            self.assertEqual(provider_addresses.refresh_for_apply("НСДИ"), ())

    def test_apply_falls_back_to_known_addresses_when_nothing_answers(self) -> None:
        self.alive.clear()
        with patch("dns.doh_lookup.find_doh_addresses", lambda *_a, **_k: _found(error="нет")):
            fresh = provider_addresses.refresh_for_apply("Malw.link", verify=self._verify)

        self.assertEqual(fresh, self.shipped)

    def test_servers_do_not_take_each_others_addresses(self) -> None:
        other = next(data for name, data in provider_addresses.tracked() if name == "AstraCat")
        taken = other["ipv4"][0]
        self.addCleanup(lambda: other["ipv4"].__setitem__(slice(None), list(other["ipv4"])))
        stored = {"Malw.link": {"ipv4": [taken, *self.NEW], "former_ipv4": []}}

        provider_addresses.adopt(stored)

        self.assertNotIn(taken, self.malw["ipv4"])
        self.assertEqual(tuple(self.malw["ipv4"]), self.NEW)


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

    def test_baseline_goes_after_the_code_and_learned_beats_both(self) -> None:
        provider_addresses._BASELINE = {"Malw.link": self.NEW}

        self.assertEqual(
            provider_addresses.current_ipv4("Malw.link", self.malw, {}), (*self.shipped, *self.NEW)
        )
        learned = {"Malw.link": {"ipv4": ["8.8.8.8"], "former_ipv4": []}}
        self.assertEqual(provider_addresses.current_ipv4("Malw.link", self.malw, learned), ("8.8.8.8",))

    def test_build_addresses_never_move_adapters_off_a_code_address(self) -> None:
        # Машина сборки стоит в другой стране: закрытый ей адрес у человека может работать.
        provider_addresses._BASELINE = {"Malw.link": self.NEW}

        self.assertEqual(provider_addresses.address_replacements({}), {})


class ApplyWorkerUsesFreshAddressesTests(unittest.TestCase):
    def test_apply_writes_the_address_found_at_the_moment_of_the_press(self) -> None:
        from dns.page_workers import DnsApplyWorker
        from dns.state import DnsCommandResult

        log: list = []

        def apply_dns(adapters, ipv4, ipv6):
            log.append(("apply", list(ipv4)))
            return DnsCommandResult(success=True, affected_count=len(adapters), total_count=len(adapters))

        data = {"ipv4": ["9.9.9.9"], "ipv6": [], "doh": "https://example.test/dns-query"}
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

    def test_server_without_doh_is_not_asked(self) -> None:
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
