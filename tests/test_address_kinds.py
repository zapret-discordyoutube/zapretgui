from __future__ import annotations

import unittest

from utils.address_kinds import (
    BLOCK_STUB_OWNERS,
    AddressKind,
    address_kind,
    block_stub_owner,
    is_stub_address,
)


class AddressKindTests(unittest.TestCase):
    def test_every_kind_is_recognised(self) -> None:
        cases = {
            "93.184.216.34": AddressKind.PUBLIC,
            "2606:2800:220:1::1": AddressKind.PUBLIC,
            "195.82.146.214": AddressKind.BLOCK_STUB,
            "198.18.0.5": AddressKind.FAKE_IP,
            "127.0.0.1": AddressKind.SELF,
            "0.0.0.0": AddressKind.SELF,
            "::1": AddressKind.SELF,
            "192.168.1.1": AddressKind.LOCAL,
            "169.254.10.10": AddressKind.LOCAL,
            "fe80::1": AddressKind.LOCAL,
            "100.64.0.1": AddressKind.CARRIER,
            "240.0.0.1": AddressKind.SERVICE,
            "224.0.0.1": AddressKind.SERVICE,
            "не адрес": AddressKind.INVALID,
            "": AddressKind.INVALID,
        }
        for ip, kind in cases.items():
            with self.subTest(ip=ip):
                self.assertEqual(address_kind(ip), kind)

    def test_known_stub_wins_over_its_network(self) -> None:
        """10.10.10.10 — частный адрес, но это известная заглушка, и подпись у неё своя."""
        self.assertEqual(address_kind("10.10.10.10"), AddressKind.BLOCK_STUB)
        self.assertEqual(block_stub_owner("10.10.10.10"), "внутренняя заглушка")
        self.assertEqual(block_stub_owner("8.8.8.8"), "")

    def test_fake_ip_of_users_vpn_is_not_a_provider_stub(self) -> None:
        self.assertFalse(is_stub_address("198.18.0.5"))

    def test_addresses_a_public_site_cannot_have_are_stubs(self) -> None:
        for ip in ("195.82.146.214", "127.0.0.1", "192.168.1.1", "100.64.0.1", "240.0.0.1"):
            with self.subTest(ip=ip):
                self.assertTrue(is_stub_address(ip))
        for ip in ("93.184.216.34", "не адрес"):
            with self.subTest(ip=ip):
                self.assertFalse(is_stub_address(ip))

    def test_every_listed_stub_has_an_owner(self) -> None:
        for ip, owner in BLOCK_STUB_OWNERS.items():
            with self.subTest(ip=ip):
                self.assertTrue(owner)
                self.assertEqual(address_kind(ip), AddressKind.BLOCK_STUB)


if __name__ == "__main__":
    unittest.main()
