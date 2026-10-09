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
            patch.object(hosts_module, "unreachable_relays", return_value=["144.31.82.230"]),
            patch.object(hosts_module.HostsManager, "apply_domain_ip_rows") as write,
        ):
            self.assertFalse(manager.apply_service_dns_selections({"ChatGPT": "zapret_dns"}))
        write.assert_not_called()
        self.assertIn("144.31.82.230", statuses[-1])
        self.assertIn("Файл hosts не изменён", statuses[-1])


if __name__ == "__main__":
    unittest.main()
