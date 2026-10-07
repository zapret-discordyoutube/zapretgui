from __future__ import annotations

import unittest
from ipaddress import ip_address, ip_network
from unittest.mock import patch

from utils import dns_interception
from utils.dns_wire import (
    FAILURE_TIMEOUT,
    STATUS_NXDOMAIN,
    STATUS_OK,
    STATUS_TIMEOUT,
    DnsQueryResult,
)


class CanaryTests(unittest.TestCase):
    def test_canary_address_is_from_documentation_network(self) -> None:
        """Настоящего сервера по этому адресу быть не может (RFC 5737)."""
        self.assertIn(ip_address(dns_interception.CANARY_SERVER), ip_network("192.0.2.0/24"))

    def _ask(self, result: DnsQueryResult) -> tuple[bool, list]:
        calls: list = []

        def fake_query(server, name, rtype, **kwargs):
            calls.append((server, name, kwargs))
            return result

        with patch.object(dns_interception, "query_udp", fake_query):
            return dns_interception.canary_answered("example.com"), calls

    def test_any_answer_means_interception_even_no_such_site(self) -> None:
        for status, rcode in ((STATUS_OK, 0), (STATUS_NXDOMAIN, 3)):
            with self.subTest(status=status):
                answered, calls = self._ask(DnsQueryResult(status=status, rcode=rcode))
                self.assertTrue(answered)
                self.assertEqual(calls[0][0], dns_interception.CANARY_SERVER)
                self.assertEqual(calls[0][1], "example.com")

    def test_silence_means_no_interception(self) -> None:
        answered, _calls = self._ask(DnsQueryResult(status=STATUS_TIMEOUT, failure=FAILURE_TIMEOUT))

        self.assertFalse(answered)


if __name__ == "__main__":
    unittest.main()
