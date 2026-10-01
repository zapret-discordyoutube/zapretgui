from __future__ import annotations

import socket
import unittest

from utils import cert_names


def _tlv(tag: int, value: bytes) -> bytes:
    size = len(value)
    if size < 0x80:
        return bytes([tag, size]) + value
    raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(raw)]) + raw + value


def _san_extension(names: list[bytes], *, critical: bool = False) -> bytes:
    sequence = _tlv(0x30, b"".join(names))
    body = b"\x06\x03\x55\x1d\x11" + (b"\x01\x01\xff" if critical else b"") + _tlv(0x04, sequence)
    return _tlv(0x30, body)


def _cn(value: str) -> bytes:
    return _tlv(0x31, _tlv(0x30, b"\x06\x03\x55\x04\x03" + _tlv(0x0C, value.encode())))


class CertNamesTests(unittest.TestCase):
    def test_dns_names_and_addresses_are_read(self) -> None:
        der = b"\x30\x82junk" + _san_extension(
            [
                _tlv(0x82, b"Example.com"),
                _tlv(0x82, b"*.example.com"),
                _tlv(0x87, socket.inet_aton("1.2.3.4")),
                _tlv(0x81, b"mail@example.com"),  # почта — не домен, пропускается
            ]
        )

        self.assertEqual(cert_names.subject_alt_names(der), ("example.com", "*.example.com", "1.2.3.4"))

    def test_critical_flag_and_long_lists(self) -> None:
        names = [_tlv(0x82, f"host{index}.example.com".encode()) for index in range(40)]
        der = _san_extension(names, critical=True)

        found = cert_names.subject_alt_names(der)

        self.assertEqual(len(found), 40)
        self.assertEqual(found[-1], "host39.example.com")

    def test_broken_certificate_gives_empty_list(self) -> None:
        der = _san_extension([_tlv(0x82, b"example.com")])

        self.assertEqual(cert_names.subject_alt_names(der[:-4]), ())
        self.assertEqual(cert_names.subject_alt_names(b""), ())

    def test_common_name_is_owner_not_issuer(self) -> None:
        der = _cn("Issuer CA") + _cn("site.example")

        self.assertEqual(cert_names.common_name(der), "site.example")
        self.assertEqual(cert_names.common_name(_cn("only.example")), "only.example")
        self.assertEqual(cert_names.common_name(b"\x00"), "")

    def test_closed_port_is_reported_not_raised(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        result = cert_names.fetch_cert_names("127.0.0.1", port=port, timeout_s=1)

        self.assertFalse(result.ok)
        self.assertEqual(result.kind, cert_names.KIND_REFUSED)

    def test_bad_address_is_reported(self) -> None:
        self.assertEqual(cert_names.fetch_cert_names("nope").kind, cert_names.KIND_ERROR)


if __name__ == "__main__":
    unittest.main()
