from __future__ import annotations

import unittest
from unittest.mock import patch

from hosts import relay_check


class HostsRelayCheckTests(unittest.TestCase):
    ROWS = [
        ("chatgpt.com", "144.31.82.230"),
        ("api.openai.com", "144.31.82.230"),
        ("auth.openai.com", "144.31.82.230"),
        ("cdn.openai.com", "104.18.33.45"),
    ]

    RELAYS = {"144.31.82.230"}

    def test_relay_is_what_the_catalog_calls_a_relay(self) -> None:
        self.assertEqual(relay_check.relay_addresses(self.ROWS, self.RELAYS), ["144.31.82.230"])
        self.assertEqual(relay_check.relay_addresses(self.ROWS, set()), [])

    def test_shared_site_address_is_not_a_relay(self) -> None:
        # Настоящий адрес сайта бывает общим для десятка имён и из России не отвечает
        # (CloudFront у Comss, 35.228.34.253 у GeoHide). В 21.1.7.116–119 его считали посредником.
        rows = [("a%d.example" % i, "65.9.46.4") for i in range(12)] + self.ROWS
        with patch.object(relay_check, "_connects", return_value=False) as connects:
            self.assertEqual(relay_check.unreachable_relays(rows, self.RELAYS), ["144.31.82.230"])
        self.assertEqual([call.args[0] for call in connects.call_args_list], ["144.31.82.230"])

    def test_blocked_relay_is_reported(self) -> None:
        with patch.object(relay_check, "_connects", return_value=False):
            self.assertEqual(relay_check.unreachable_relays(self.ROWS, self.RELAYS), ["144.31.82.230"])
        with patch.object(relay_check, "_connects", return_value=True):
            self.assertEqual(relay_check.unreachable_relays(self.ROWS, self.RELAYS), [])

    def test_rows_without_a_relay_are_not_probed(self) -> None:
        with patch.object(relay_check, "_connects") as connects:
            self.assertEqual(relay_check.unreachable_relays([("a.example", "1.2.3.4")], self.RELAYS), [])
            connects.assert_not_called()

    def test_catalog_names_only_real_relays(self) -> None:
        from dns import zapret_dns_server
        from hosts import proxy_domains

        relays = proxy_domains.get_relay_addresses()
        # Свой Zapret DNS — нынешний адрес на месте метки каталога (config.zapret_dns).
        for address in (zapret_dns_server.active_ipv4()[0], "91.132.224.167", "103.137.248.145", "80.74.30.162", "217.60.179.6", "159.194.200.33"):
            self.assertIn(address, relays)
        # Обычные адреса сайтов из профилей Comss и GeoHide: на них ссылались отказы у пользователя.
        for address in ("108.156.22.111", "65.9.46.4", "65.9.46.49", "40.180.22.93", "52.50.163.132", "52.208.113.195", "35.228.34.253"):
            self.assertNotIn(address, relays)

    def test_selection_with_a_blocked_relay_is_not_written(self) -> None:
        from hosts import hosts as hosts_module

        manager = hosts_module.HostsManager.__new__(hosts_module.HostsManager)
        statuses: list[str] = []
        manager.set_status = statuses.append
        with (
            patch.object(hosts_module, "_build_service_selection_rows", return_value=(list(self.ROWS), True)),
            patch.object(hosts_module, "get_service_domain_ip_rows", return_value=list(self.ROWS)),
            patch.object(hosts_module, "service_has_proxy_profiles", return_value=True),
            patch.object(hosts_module, "safe_read_hosts_file", return_value=""),
            patch.object(hosts_module, "get_relay_addresses", return_value=set(self.RELAYS)),
            patch.object(hosts_module, "unreachable_relays", return_value=["144.31.82.230"]),
            patch.object(hosts_module, "internet_reachable", return_value=True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
            patch.object(hosts_module.HostsManager, "apply_domain_ip_rows") as write,
        ):
            self.assertFalse(manager.apply_service_dns_selections({"ChatGPT": "zapret_dns"}))
        write.assert_not_called()
        self.assertIn("144.31.82.230", statuses[-1])
        self.assertIn("Файл hosts не изменён", statuses[-1])

    def test_without_internet_the_provider_is_not_blamed(self) -> None:
        # Молчат и посредник, и контрольные адреса: это нет связи, а не блокировка.
        from hosts import hosts as hosts_module

        manager = hosts_module.HostsManager.__new__(hosts_module.HostsManager)
        statuses: list[str] = []
        manager.set_status = statuses.append
        with (
            patch.object(hosts_module, "_build_service_selection_rows", return_value=(list(self.ROWS), True)),
            patch.object(hosts_module, "get_service_domain_ip_rows", return_value=list(self.ROWS)),
            patch.object(hosts_module, "service_has_proxy_profiles", return_value=True),
            patch.object(hosts_module, "safe_read_hosts_file", return_value=""),
            patch.object(hosts_module, "get_relay_addresses", return_value=set(self.RELAYS)),
            patch.object(hosts_module, "unreachable_relays", return_value=["144.31.82.230"]),
            patch.object(hosts_module, "internet_reachable", return_value=False),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
            patch.object(hosts_module.HostsManager, "apply_domain_ip_rows") as write,
        ):
            self.assertFalse(manager.apply_service_dns_selections({"ChatGPT": "zapret_dns"}))
        write.assert_not_called()
        self.assertIn("Нет связи с интернетом", statuses[-1])
        self.assertNotIn("провайдер", statuses[-1])

    def test_one_lost_attempt_is_not_a_blocked_relay(self) -> None:
        attempts = iter([False, True])
        with patch.object(relay_check, "_connect_once", side_effect=lambda _address: next(attempts)), patch.object(
            relay_check.time, "sleep"
        ):
            self.assertTrue(relay_check._connects("144.31.82.230"))

    def test_ipv6_relays_are_not_probed_without_ipv6(self) -> None:
        rows = [("chatgpt.com", "2a00:1::5")]
        with patch.object(relay_check, "_connects", return_value=False) as connects:
            self.assertEqual(relay_check.unreachable_relays(rows, {"2a00:1::5"}, ipv6=False), [])
        connects.assert_not_called()

    # Настоящий адрес Instagram стоит сразу у нескольких имён и из России без обхода
    # не отвечает. В выпуске 21.1.7.116 он считался посредником и запрещал любую правку hosts.
    DIRECT_ROWS = [
        ("instagram.com", "157.240.245.174"),
        ("www.instagram.com", "157.240.245.174"),
        ("i.instagram.com", "157.240.245.174"),
    ]

    def _apply(self, selection, *, proxy_services, rows_by_service, written=""):
        from hosts import hosts as hosts_module

        manager = hosts_module.HostsManager.__new__(hosts_module.HostsManager)
        self.statuses: list[str] = []
        manager.set_status = self.statuses.append
        probed: list[str] = []

        def build(service_dns, static_enabled=None):
            rows = [row for name in service_dns for row in rows_by_service[name]]
            return rows, bool(rows)

        def connects(address):
            probed.append(address)
            return False

        with (
            patch.object(hosts_module, "_build_service_selection_rows", side_effect=build),
            patch.object(hosts_module, "get_service_domain_ip_rows", side_effect=lambda name, _profile: list(rows_by_service[name])),
            patch.object(hosts_module, "service_has_proxy_profiles", side_effect=lambda name: name in proxy_services),
            patch.object(hosts_module, "safe_read_hosts_file", return_value=written),
            patch.object(hosts_module, "get_relay_addresses", return_value=set(self.RELAYS)),
            patch.object(relay_check, "_connects", side_effect=connects),
            patch.object(hosts_module, "internet_reachable", return_value=True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
            patch.object(hosts_module.HostsManager, "apply_domain_ip_rows", return_value=True) as write,
        ):
            ok = manager.apply_service_dns_selections(selection)
        return ok, write, probed

    def test_direct_service_address_is_not_a_relay(self) -> None:
        ok, write, probed = self._apply(
            {"Instagram": "direct"}, proxy_services=set(), rows_by_service={"Instagram": self.DIRECT_ROWS}
        )
        self.assertTrue(ok)
        write.assert_called_once()
        self.assertEqual(probed, [])

    def test_blocked_new_relay_still_stops_the_write_next_to_a_direct_service(self) -> None:
        ok, write, probed = self._apply(
            {"Instagram": "direct", "ChatGPT": "zapret_dns"},
            proxy_services={"ChatGPT"},
            rows_by_service={"Instagram": self.DIRECT_ROWS, "ChatGPT": self.ROWS},
        )
        self.assertFalse(ok)
        write.assert_not_called()
        self.assertEqual(probed, ["144.31.82.230"])

    # Как у GeoHide для Gemini: четыре посредника на имя, один из них провайдер закрыл.
    GEMINI_ROWS = [
        (domain, address)
        for domain in ("gemini.google.com", "aistudio.google.com")
        for address in ("159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88")
    ]

    def test_one_silent_relay_is_skipped_and_the_service_goes_through_the_others(self) -> None:
        from hosts import hosts as hosts_module

        manager = hosts_module.HostsManager.__new__(hosts_module.HostsManager)
        manager.set_status = lambda _text: None
        relays = {"159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88"}
        with (
            patch.object(hosts_module, "_build_service_selection_rows", return_value=(list(self.GEMINI_ROWS), True)),
            patch.object(hosts_module, "get_service_domain_ip_rows", return_value=list(self.GEMINI_ROWS)),
            patch.object(hosts_module, "service_has_proxy_profiles", return_value=True),
            patch.object(hosts_module, "safe_read_hosts_file", return_value=""),
            patch.object(hosts_module, "get_relay_addresses", return_value=relays),
            patch.object(relay_check, "_connects", side_effect=lambda address: address != "159.194.200.33"),
            patch.object(hosts_module, "internet_reachable", return_value=True),
            patch.object(hosts_module, "is_ipv6_available", return_value=True),
            patch.object(hosts_module.HostsManager, "apply_domain_ip_rows", return_value=True) as write,
        ):
            self.assertTrue(manager.apply_service_dns_selections({"Gemini AI": "geohide"}))
        written = write.call_args.args[0]
        self.assertNotIn("159.194.200.33", {ip for _domain, ip in written})
        self.assertEqual(len(written), 6)

    def test_service_without_a_single_answering_relay_is_named(self) -> None:
        ok, write, _probed = self._apply(
            {"ChatGPT": "zapret_dns"}, proxy_services={"ChatGPT"}, rows_by_service={"ChatGPT": self.ROWS}
        )
        self.assertFalse(ok)
        write.assert_not_called()
        self.assertTrue(self.statuses[-1].startswith("ChatGPT: ни один сервер-посредник"))

    def test_relay_already_written_to_hosts_does_not_block_edits(self) -> None:
        from hosts import hosts as hosts_module

        with patch.object(hosts_module, "_iter_managed_hosts_block_rows", return_value=list(self.ROWS)):
            ok, write, probed = self._apply(
                {"ChatGPT": "zapret_dns"}, proxy_services={"ChatGPT"}, rows_by_service={"ChatGPT": self.ROWS}
            )
        self.assertTrue(ok)
        write.assert_called_once()
        self.assertEqual(probed, [])


if __name__ == "__main__":
    unittest.main()
