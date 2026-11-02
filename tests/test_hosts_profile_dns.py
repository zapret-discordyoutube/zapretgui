"""Свежие посредники профиля hosts — у DNS-сервера самого профиля (hosts.profile_dns)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from hosts import profile_dns
from utils.dns_wire import TYPE_A


def _answer(values=(), answered=True):
    return SimpleNamespace(answered=answered, values=lambda _rtype: list(values))


class ProfileDnsTests(unittest.TestCase):
    def test_every_profile_server_is_in_the_dns_list_with_a_doh_template(self) -> None:
        for profile_id in profile_dns.PROFILE_SERVERS:
            with self.subTest(profile=profile_id):
                self.assertIsNotNone(profile_dns._server(profile_id))

    def test_profile_without_a_server_is_not_asked(self) -> None:
        self.assertIsNone(profile_dns.ask_profile("xbox_dns", "chatgpt.com", query=lambda *a, **k: self.fail("спрашивать некого")))
        self.assertFalse(profile_dns.has_server("xbox_dns"))
        self.assertTrue(profile_dns.has_server("comss_dns"))

    def test_doh_name_address_is_asked_first_with_the_doh_name_for_tls(self) -> None:
        asked: list[tuple] = []

        def query(address, host, rtype, *, tls_host, port, path, timeout_s):
            asked.append((address, host, rtype, tls_host, path))
            return _answer(["157.22.178.170", "206.223.246.75"])

        found = profile_dns.ask_profile("comss_dns", "chatgpt.com", query=query, host_addresses=lambda _name: ["195.133.25.16"])

        self.assertEqual(found, ["157.22.178.170", "206.223.246.75"])
        self.assertEqual(asked, [("195.133.25.16", "chatgpt.com", TYPE_A, "dns.comss.one", "/dns-query")])

    def test_next_address_is_tried_when_one_is_silent(self) -> None:
        def query(address, host, rtype, **_kwargs):
            if address == "10.0.0.1":
                raise OSError("timed out")
            return _answer(["7.7.7.1"])

        found = profile_dns.ask_profile("geohide", "gemini.google.com", query=query, host_addresses=lambda _name: ["10.0.0.1"])

        self.assertEqual(found, ["7.7.7.1"])

    def test_silent_server_gives_no_answer_rather_than_an_empty_one(self) -> None:
        found = profile_dns.ask_profile(
            "dns_ai", "chatgpt.com", query=lambda *a, **k: _answer(answered=False), host_addresses=lambda _name: []
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
