"""Поиск IP-адресов сервера по адресу DoH: кандидаты, проверка, Windows без DoH."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from dns import doh_lookup
from dns.custom_servers import parse_doh_template
from dns.doh_lookup import find_doh_addresses
from utils.dns_wire import (
    FAILURE_CERT,
    FAILURE_TIMEOUT,
    STATUS_OK,
    STATUS_TIMEOUT,
    TYPE_A,
    DnsQueryResult,
    DnsRecord,
)

TEMPLATE = parse_doh_template("https://dns.example.com:444/q")
OK = DnsQueryResult(status=STATUS_OK, rcode=0)
SILENT = DnsQueryResult(status=STATUS_TIMEOUT, failure=FAILURE_TIMEOUT)
FOREIGN = DnsQueryResult(status="error", failure=FAILURE_CERT)


def _resolver(*addresses):
    return lambda host, *, ipv6, cancel=None: [item for item in addresses if ipv6 or ":" not in item]


def _probe(answers: dict):
    return lambda address, template, cancel=None: answers.get(address, SILENT)


class FindDohAddressesTests(unittest.TestCase):
    def test_only_addresses_that_answer_doh_are_kept(self) -> None:
        found = find_doh_addresses(
            TEMPLATE,
            ipv6=True,
            resolve=_resolver("203.0.113.5", "198.51.100.9", "2001:db8::5", "203.0.113.6"),
            probe=_probe({"203.0.113.5": OK, "203.0.113.6": OK, "2001:db8::5": OK, "198.51.100.9": FOREIGN}),
        )

        self.assertTrue(found.found)
        self.assertEqual(found.ipv4, ("203.0.113.5", "203.0.113.6"))
        self.assertEqual(found.ipv6, ("2001:db8::5",))
        self.assertEqual(found.notice, "")

    def test_no_more_than_two_addresses_of_each_family(self) -> None:
        addresses = [f"203.0.113.{index}" for index in range(1, 6)]
        found = find_doh_addresses(TEMPLATE, resolve=_resolver(*addresses), probe=_probe(dict.fromkeys(addresses, OK)))

        self.assertEqual(found.ipv4, ("203.0.113.1", "203.0.113.2"))

    def test_ipv6_addresses_are_skipped_without_ipv6_route(self) -> None:
        asked = []

        def probe(address, template, cancel=None):
            asked.append(address)
            return OK

        found = find_doh_addresses(TEMPLATE, ipv6=False, resolve=_resolver("203.0.113.5", "2001:db8::5"), probe=probe)

        self.assertEqual((found.ipv4, found.ipv6), (("203.0.113.5",), ()))
        self.assertEqual(asked, ["203.0.113.5"])

    def test_unknown_name_is_explained(self) -> None:
        found = find_doh_addresses(TEMPLATE, resolve=_resolver(), probe=_probe({}))

        self.assertFalse(found.found)
        self.assertIn("dns.example.com", found.error)
        self.assertIn("имя не находится", found.error)

    def test_server_that_does_not_answer_doh_is_explained_with_the_reason(self) -> None:
        found = find_doh_addresses(TEMPLATE, resolve=_resolver("203.0.113.5"), probe=_probe({"203.0.113.5": FOREIGN}))

        self.assertFalse(found.found)
        self.assertIn("203.0.113.5", found.error)
        self.assertIn("чужой сертификат", found.error)

    def test_server_given_by_address_is_checked_without_name_lookup(self) -> None:
        template = parse_doh_template("https://9.9.9.9/dns-query")

        def resolve(*_args, **_kwargs):
            raise AssertionError("у адреса нечего искать по имени")

        found = find_doh_addresses(template, resolve=resolve, probe=_probe({"9.9.9.9": OK}))

        self.assertEqual(found.ipv4, ("9.9.9.9",))

    def test_windows_without_doh_keeps_addresses_that_answer_plain_dns(self) -> None:
        found = find_doh_addresses(
            TEMPLATE,
            doh_supported=False,
            resolve=_resolver("203.0.113.5", "203.0.113.6"),
            probe=_probe({"203.0.113.5": OK, "203.0.113.6": OK}),
            plain=lambda address, cancel=None: OK if address == "203.0.113.6" else SILENT,
        )

        self.assertEqual(found.ipv4, ("203.0.113.6",))
        self.assertIn("обычным DNS", found.notice)

    def test_windows_without_doh_rejects_a_doh_only_server(self) -> None:
        found = find_doh_addresses(
            TEMPLATE,
            doh_supported=False,
            resolve=_resolver("203.0.113.5"),
            probe=_probe({"203.0.113.5": OK}),
            plain=lambda address, cancel=None: SILENT,
        )

        self.assertFalse(found.found)
        self.assertIn("Windows 11", found.error)


class NetworkCallsTests(unittest.TestCase):
    def test_probe_asks_the_address_with_the_server_name_port_and_path(self) -> None:
        with patch.object(doh_lookup, "query_doh", return_value=OK) as query:
            doh_lookup.probe_doh("203.0.113.5", TEMPLATE)

        self.assertEqual(query.call_args.args[0], "203.0.113.5")
        self.assertEqual(
            {key: query.call_args.kwargs[key] for key in ("tls_host", "port", "path")},
            {"tls_host": "dns.example.com", "port": 444, "path": "/q"},
        )

    def test_candidates_come_from_reference_servers_and_system_dns(self) -> None:
        def answer(resolver, host, rtype, **_kwargs):
            if resolver != "1.1.1.1":
                return SILENT
            value = "203.0.113.5" if rtype == TYPE_A else "2001:db8::5"
            return DnsQueryResult(status=STATUS_OK, rcode=0, records=(DnsRecord(host, rtype, 60, value),))

        system = [(2, 1, 6, "", ("203.0.113.6", 443)), (2, 1, 6, "", ("203.0.113.5", 443))]
        with patch.object(doh_lookup, "query_doh", side_effect=answer), patch.object(
            doh_lookup.socket, "getaddrinfo", return_value=system
        ):
            with_ipv6 = doh_lookup.resolve_candidates("dns.example.com", ipv6=True)
            without_ipv6 = doh_lookup.resolve_candidates("dns.example.com", ipv6=False)

        self.assertEqual(with_ipv6, ["203.0.113.5", "2001:db8::5", "203.0.113.6"])
        self.assertEqual(without_ipv6, ["203.0.113.5", "203.0.113.6"])

    def test_blocked_reference_servers_do_not_hold_the_answer(self) -> None:
        # Закрытые на линии серверы молчат до тайм-аута; ответ первого живого ждать их не должен.
        release = threading.Event()
        self.addCleanup(release.set)

        def answer(resolver, host, rtype, **_kwargs):
            if resolver != "94.140.14.140":
                release.wait(5)
                return SILENT
            return DnsQueryResult(status=STATUS_OK, rcode=0, records=(DnsRecord(host, rtype, 60, "203.0.113.5"),))

        started = time.monotonic()
        with patch.object(doh_lookup, "query_doh", side_effect=answer), patch.object(
            doh_lookup.socket, "getaddrinfo", side_effect=OSError("нет сети")
        ):
            found = doh_lookup.resolve_candidates("dns.example.com", ipv6=False)

        self.assertEqual(found, ["203.0.113.5"])
        self.assertLess(time.monotonic() - started, 2.0)

    def test_system_dns_failure_is_not_an_error(self) -> None:
        with patch.object(doh_lookup, "query_doh", return_value=SILENT), patch.object(
            doh_lookup.socket, "getaddrinfo", side_effect=OSError("нет сети")
        ):
            self.assertEqual(doh_lookup.resolve_candidates("dns.example.com", ipv6=False), [])


if __name__ == "__main__":
    unittest.main()
