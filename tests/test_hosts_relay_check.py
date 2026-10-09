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

    def test_relay_is_the_address_many_names_point_to(self) -> None:
        self.assertEqual(relay_check.relay_addresses(self.ROWS), ["144.31.82.230"])
        self.assertEqual(relay_check.relay_addresses(self.ROWS[2:]), [])

    def test_blocked_relay_is_reported(self) -> None:
        with patch.object(relay_check, "_connects", return_value=False):
            self.assertEqual(relay_check.unreachable_relays(self.ROWS), ["144.31.82.230"])
        with patch.object(relay_check, "_connects", return_value=True):
            self.assertEqual(relay_check.unreachable_relays(self.ROWS), [])

    def test_rows_without_a_relay_are_not_probed(self) -> None:
        with patch.object(relay_check, "_connects") as connects:
            self.assertEqual(relay_check.unreachable_relays([("a.example", "1.2.3.4")]), [])
            connects.assert_not_called()

    def test_selection_with_a_blocked_relay_is_not_written(self) -> None:
        from hosts import hosts as hosts_module

        manager = hosts_module.HostsManager.__new__(hosts_module.HostsManager)
        statuses: list[str] = []
        manager.set_status = statuses.append
        with (
            patch.object(hosts_module, "_build_service_selection_rows", return_value=(list(self.ROWS), True)),
            patch.object(hosts_module, "service_has_proxy_profiles", return_value=True),
            patch.object(hosts_module, "safe_read_hosts_file", return_value=""),
            patch.object(hosts_module, "unreachable_relays", return_value=["144.31.82.230"]),
            patch.object(hosts_module.HostsManager, "apply_domain_ip_rows") as write,
        ):
            self.assertFalse(manager.apply_service_dns_selections({"ChatGPT": "zapret_dns"}))
        write.assert_not_called()
        self.assertIn("144.31.82.230", statuses[-1])
        self.assertIn("Файл hosts не изменён", statuses[-1])

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
            patch.object(hosts_module, "service_has_proxy_profiles", side_effect=lambda name: name in proxy_services),
            patch.object(hosts_module, "safe_read_hosts_file", return_value=written),
            patch.object(relay_check, "_connects", side_effect=connects),
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
