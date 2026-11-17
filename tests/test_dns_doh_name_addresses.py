import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dns import doh_name_addresses as module


class DohNameAddressesTests(unittest.TestCase):
    def setUp(self) -> None:
        module._host_cache.clear()
        self.addCleanup(module._host_cache.clear)

    def _ask(self, values):
        answer = SimpleNamespace(values=lambda _type: values)
        return patch("utils.dns_reference.reference_ask", lambda *_a, **_k: (lambda _host, _type: answer))

    def test_found_addresses_are_remembered(self) -> None:
        with self._ask(["37.230.192.51"]):
            self.assertEqual(module.doh_host_addresses("geohide.ru"), ["37.230.192.51"])
        # Второй вопрос уже не ходит в сеть.
        with patch("utils.dns_reference.reference_ask", side_effect=AssertionError("в сеть не ходить")):
            self.assertEqual(module.doh_host_addresses("geohide.ru"), ["37.230.192.51"])

    def test_failed_lookup_is_not_remembered_for_an_hour(self) -> None:
        with self._ask([]):
            self.assertEqual(module.doh_host_addresses("geohide.ru"), [])
        with self._ask(["37.230.192.51"]):
            self.assertEqual(module.doh_host_addresses("geohide.ru"), ["37.230.192.51"])


if __name__ == "__main__":
    unittest.main()
