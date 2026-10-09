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

    def _check(self, ipv4, ipv6=(), templates=None, **answers) -> str:
        a, b, c = self._probe(**answers)
        with a, b, c:
            return reachability.unreachable_message(list(ipv4), list(ipv6), templates)

    def test_silent_server_is_reported_with_its_address(self) -> None:
        message = self._check(["144.31.82.230"])

        self.assertIn("144.31.82.230", message)
        self.assertIn("DNS не изменён", message)

    def test_any_working_way_is_enough(self) -> None:
        # Провайдер режет открытый DNS, а шифрованный проходит: сервер годится.
        self.assertEqual(self._check(["9.9.9.9"], templates={"9.9.9.9": "https://dns.quad9.net/dns-query"}, doh=True), "")
        self.assertEqual(self._check(["9.9.9.9"], udp=True), "")
        self.assertEqual(self._check(["9.9.9.9"], tcp=True), "")

    def test_doh_is_asked_only_when_the_server_has_a_template(self) -> None:
        a, b, c = self._probe()
        with a, b, c as doh:
            reachability.unreachable_message(["9.9.9.9"], [], {})
            doh.assert_not_called()
            reachability.unreachable_message(["9.9.9.9"], [], {"9.9.9.9": "https://dns.example.com:444/q"})
            self.assertEqual(doh.call_args.kwargs["tls_host"], "dns.example.com")
            self.assertEqual((doh.call_args.kwargs["port"], doh.call_args.kwargs["path"]), (444, "/q"))

    def test_local_addresses_and_reset_are_not_probed(self) -> None:
        a, b, c = self._probe()
        with a as udp, b, c:
            # Возврат автоматических DNS, движок на этом компьютере и роутер — проверять нечего.
            self.assertEqual(reachability.unreachable_message([], []), "")
            self.assertEqual(reachability.unreachable_message(["127.0.0.1"], ["::1"]), "")
            self.assertEqual(reachability.unreachable_message(["192.168.1.1"], []), "")
            udp.assert_not_called()

    def test_ipv6_is_checked_only_when_there_is_no_ipv4(self) -> None:
        a, b, c = self._probe()
        with a as udp, b, c:
            reachability.unreachable_message(["9.9.9.9"], ["2620:fe::fe"])
            self.assertEqual([call.args[0] for call in udp.call_args_list], ["9.9.9.9"])
            udp.reset_mock()
            reachability.unreachable_message([], ["2620:fe::fe"])
            self.assertEqual([call.args[0] for call in udp.call_args_list], ["2620:fe::fe"])


if __name__ == "__main__":
    unittest.main()
