"""Пробный вопрос движка шифрованного DNS: NS для случайного имени, ждём «имени нет» (dns.engine_check)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dns import engine_check
from utils.dns_wire import TYPE_NS

DOH = "https://dns.example/dns-query"


def _answers(**by_address):
    asked: list = []

    def query(address, name, rtype, **kwargs):
        asked.append((address, name, rtype, kwargs))
        return SimpleNamespace(status=by_address[address])

    return query, asked


class EngineCheckTests(unittest.TestCase):
    def test_question_is_ns_for_a_random_name_like_the_engines(self) -> None:
        query, asked = _answers(**{"192.0.2.1": "nxdomain"})

        engine_check.engine_rejects(DOH, ["192.0.2.1"], query=query)

        address, name, rtype, kwargs = asked[0]
        self.assertEqual(rtype, TYPE_NS)
        self.assertRegex(name, r"^[a-z]{16}\.test\.dnscrypt$")
        self.assertEqual((kwargs["tls_host"], kwargs["path"]), ("dns.example", "/dns-query"))

    def test_empty_or_refused_answer_means_the_engine_refuses_the_server(self) -> None:
        for status in ("empty", "refused", "ok"):
            with self.subTest(status=status):
                query, _ = _answers(**{"192.0.2.1": status})
                self.assertTrue(engine_check.engine_rejects(DOH, ["192.0.2.1"], query=query))

    def test_one_address_that_says_no_such_name_is_enough_for_the_engine(self) -> None:
        query, _ = _answers(**{"192.0.2.1": "empty", "192.0.2.2": "nxdomain"})
        self.assertFalse(engine_check.engine_rejects(DOH, ["192.0.2.1", "192.0.2.2"], query=query))

    def test_silent_server_is_not_blamed_on_its_answer(self) -> None:
        query, _ = _answers(**{"192.0.2.1": "timeout", "192.0.2.2": "error"})
        self.assertFalse(engine_check.engine_rejects(DOH, ["192.0.2.1", "192.0.2.2"], query=query))

    def test_nothing_to_ask_is_not_a_refusal(self) -> None:
        query, asked = _answers()
        self.assertFalse(engine_check.engine_rejects("", ["192.0.2.1"], query=query))
        self.assertFalse(engine_check.engine_rejects(DOH, ["2001:db8::1"], query=query))
        self.assertFalse(engine_check.engine_rejects("http://dns.example/dns-query", ["192.0.2.1"], query=query))
        self.assertEqual(asked, [])

    def test_failing_query_counts_as_silent(self) -> None:
        def query(*_args, **_kwargs):
            raise RuntimeError("boom")

        self.assertFalse(engine_check.engine_rejects(DOH, ["192.0.2.1"], query=query))


if __name__ == "__main__":
    unittest.main()
