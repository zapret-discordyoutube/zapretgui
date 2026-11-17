from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dns import reachability


def _result(answered: bool):
    return SimpleNamespace(answered=answered)


class DnsReachabilityTests(unittest.TestCase):
    def _probe(self, *, udp=False, tcp=False, doh=False):
        return (
            patch.object(reachability, "query_udp", return_value=_result(udp)),
            patch.object(reachability, "query_tcp", return_value=_result(tcp)),
            patch.object(reachability, "query_doh", return_value=_result(doh)),
        )

    def _check(self, ipv4, ipv6=(), templates=None, **answers) -> reachability.ServerReach:
        a, b, c = self._probe(**answers)
        with a, b, c:
            return reachability.check_servers(list(ipv4), list(ipv6), templates)

    def test_silent_server_is_reported_with_its_address(self) -> None:
        message = self._check(["144.31.82.230"]).blocked

        self.assertIn("144.31.82.230", message)
        self.assertIn("DNS не изменён", message)

    def test_any_working_way_is_enough(self) -> None:
        # Провайдер режет открытый DNS, а шифрованный проходит: сервер годится.
        quad9 = {"9.9.9.9": "https://dns.quad9.net/dns-query"}
        self.assertEqual(self._check(["9.9.9.9"], templates=quad9, doh=True), reachability.ServerReach())
        self.assertEqual(self._check(["9.9.9.9"], udp=True), reachability.ServerReach())
        self.assertEqual(self._check(["9.9.9.9"], tcp=True), reachability.ServerReach())

    def test_server_that_answers_only_the_speed_test_name_is_alive(self) -> None:
        """Плитка показала сервер живым по google.com, а проверка отказывала по example.com."""
        def udp(address, name, *_args, **_kwargs):
            return _result(name == reachability.SPEED_TEST_NAME)

        with patch.object(reachability, "query_udp", side_effect=udp), patch.object(
            reachability, "query_tcp", return_value=_result(False)
        ):
            self.assertEqual(reachability.check_servers(["135.106.217.200"], [], {}), reachability.ServerReach())

    def test_closed_doh_with_working_plain_dns_is_reported(self) -> None:
        """Windows откатится на обычный DNS молча — об этом должна сказать программа."""
        quad9 = {"9.9.9.9": "https://dns.quad9.net/dns-query"}

        self.assertEqual(self._check(["9.9.9.9"], templates=quad9, udp=True), reachability.ServerReach(doh_closed=True))
        self.assertFalse(self._check(["9.9.9.9"], templates=quad9, udp=True, doh=True).doh_closed)
        # Без шаблона шифровать нечем — и тревожиться не о чем.
        self.assertFalse(self._check(["9.9.9.9"], udp=True).doh_closed)

    def test_doh_is_asked_only_when_the_server_has_a_template(self) -> None:
        a, b, c = self._probe()
        with a, b, c as doh:
            reachability.check_servers(["9.9.9.9"], [], {})
            doh.assert_not_called()
            reachability.check_servers(["9.9.9.9"], [], {"9.9.9.9": "https://dns.example.com:444/q"})
            self.assertEqual(doh.call_args.kwargs["tls_host"], "dns.example.com")
            self.assertEqual((doh.call_args.kwargs["port"], doh.call_args.kwargs["path"]), (444, "/q"))

    def test_local_addresses_and_reset_are_not_probed(self) -> None:
        a, b, c = self._probe()
        with a as udp, b, c:
            # Возврат автоматических DNS, движок на этом компьютере и роутер — проверять нечего.
            self.assertEqual(reachability.check_servers([], []), reachability.ServerReach())
            self.assertEqual(reachability.check_servers(["127.0.0.1"], ["::1"]), reachability.ServerReach())
            self.assertEqual(reachability.check_servers(["192.168.1.1"], []), reachability.ServerReach())
            udp.assert_not_called()

    def test_ipv6_is_checked_only_when_there_is_no_ipv4(self) -> None:
        a, b, c = self._probe()
        with a as udp, b, c:
            reachability.check_servers(["9.9.9.9"], ["2620:fe::fe"])
            self.assertEqual({call.args[0] for call in udp.call_args_list}, {"9.9.9.9"})
            udp.reset_mock()
            reachability.check_servers([], ["2620:fe::fe"])
            self.assertEqual({call.args[0] for call in udp.call_args_list}, {"2620:fe::fe"})


if __name__ == "__main__":
    unittest.main()
