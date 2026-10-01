from __future__ import annotations

import socket
import struct
import threading
import unittest

from utils import dns_wire


def _name(name: str) -> bytes:
    return b"".join(bytes([len(part)]) + part.encode() for part in name.split(".")) + b"\x00"


def _response(query_id: int, *, rcode: int = 0, answers: list[bytes] | None = None, flags: int = 0x8180) -> bytes:
    answers = answers or []
    header = struct.pack("!HHHHHH", query_id, flags | rcode, 1, len(answers), 0, 0)
    question = _name("example.com") + struct.pack("!HH", 1, 1)
    return header + question + b"".join(answers)


def _record(rtype: int, data: bytes, *, name: bytes = b"\xc0\x0c", ttl: int = 60) -> bytes:
    return name + struct.pack("!HHIH", rtype, 1, ttl, len(data)) + data


class ParseTests(unittest.TestCase):
    def test_query_has_question_and_edns(self) -> None:
        packet = dns_wire.build_query(0x1234, "Example.com.", dns_wire.TYPE_AAAA)

        query_id, flags, questions, _an, _ns, additional = struct.unpack("!HHHHHH", packet[:12])
        self.assertEqual((query_id, flags, questions, additional), (0x1234, 0x0100, 1, 1))
        self.assertIn(_name("Example.com") + struct.pack("!HH", 28, 1), packet)

    def test_cyrillic_domain_is_sent_as_punycode(self) -> None:
        self.assertIn(b"\x08xn--p1ai", dns_wire.encode_name("пример.рф"))

    def test_bad_names_are_rejected(self) -> None:
        for name in ("", "a" * 64 + ".com", "a..b"):
            with self.subTest(name=name), self.assertRaises(dns_wire.DnsWireError):
                dns_wire.encode_name(name)

    def test_addresses_and_compressed_cname_are_parsed(self) -> None:
        cname_target = b"\x03www" + b"\xc0\x0c"  # www + ссылка на example.com в вопросе
        packet = _response(
            7,
            answers=[
                _record(dns_wire.TYPE_CNAME, cname_target),
                _record(dns_wire.TYPE_A, socket.inet_aton("93.184.216.34")),
                _record(dns_wire.TYPE_AAAA, socket.inet_pton(socket.AF_INET6, "2606:2800:220:1::1")),
            ],
        )

        message = dns_wire.parse_response(packet)

        self.assertTrue(message.is_response)
        self.assertEqual(message.question, "example.com")
        self.assertEqual(
            [(record.rtype, record.value) for record in message.answers],
            [(5, "www.example.com"), (1, "93.184.216.34"), (28, "2606:2800:220:1::1")],
        )

    def test_txt_and_ptr_records(self) -> None:
        packet = _response(
            1,
            answers=[
                _record(dns_wire.TYPE_TXT, b"\x05hello\x06 world"),
                _record(dns_wire.TYPE_PTR, _name("dns.google")),
            ],
        )

        message = dns_wire.parse_response(packet)

        self.assertEqual([record.value for record in message.answers], ["hello world", "dns.google"])

    def test_pointer_loop_does_not_hang(self) -> None:
        packet = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0) + b"\xc0\x0c" + struct.pack("!HH", 1, 1)

        with self.assertRaises(dns_wire.DnsWireError):
            dns_wire.parse_response(packet)

    def test_truncated_packets_are_rejected(self) -> None:
        packet = _response(1, answers=[_record(dns_wire.TYPE_A, socket.inet_aton("1.2.3.4"))])

        for size in (5, len(packet) - 2):
            with self.subTest(size=size), self.assertRaises(dns_wire.DnsWireError):
                dns_wire.parse_response(packet[:size])

    def test_reverse_names(self) -> None:
        self.assertEqual(dns_wire.reverse_name("8.8.4.4"), "4.4.8.8.in-addr.arpa")
        self.assertTrue(dns_wire.reverse_name("2001:db8::1").endswith(".ip6.arpa"))


class _FakeDnsServer:
    """Маленький UDP+TCP DNS-сервер на localhost: отвечает тем, что вернёт ``reply``."""

    def __init__(self, reply) -> None:
        self._reply = reply
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.port = self.udp.getsockname()[1]
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.bind(("127.0.0.1", self.port))
        self.tcp.listen(1)
        self.udp.settimeout(3)
        self.tcp.settimeout(3)
        self.transports: list[str] = []
        self._threads = [threading.Thread(target=self._serve_udp, daemon=True), threading.Thread(target=self._serve_tcp, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _serve_udp(self) -> None:
        try:
            while True:
                packet, address = self.udp.recvfrom(4096)
                self.transports.append("udp")
                answer = self._reply(struct.unpack("!H", packet[:2])[0], "udp")
                if answer is not None:
                    self.udp.sendto(answer, address)
        except OSError:
            pass

    def _serve_tcp(self) -> None:
        try:
            connection, _address = self.tcp.accept()
            with connection:
                size = struct.unpack("!H", connection.recv(2))[0]
                packet = connection.recv(size)
                self.transports.append("tcp")
                answer = self._reply(struct.unpack("!H", packet[:2])[0], "tcp")
                connection.sendall(struct.pack("!H", len(answer)) + answer)
        except OSError:
            pass

    def close(self) -> None:
        self.udp.close()
        self.tcp.close()


class QueryServerTests(unittest.TestCase):
    def _server(self, reply) -> _FakeDnsServer:
        server = _FakeDnsServer(reply)
        self.addCleanup(server.close)
        return server

    def test_answer_is_returned_with_time(self) -> None:
        server = self._server(lambda query_id, _t: _response(query_id, answers=[_record(1, socket.inet_aton("1.2.3.4"))]))

        result = dns_wire.query_server("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=2)

        self.assertEqual(result.status, dns_wire.STATUS_OK)
        self.assertEqual(result.values(dns_wire.TYPE_A), ("1.2.3.4",))
        self.assertIsNotNone(result.elapsed_ms)

    def test_nxdomain_and_empty_are_different_statuses(self) -> None:
        server = self._server(lambda query_id, _t: _response(query_id, rcode=3))
        result = dns_wire.query_server("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=2)
        self.assertEqual(result.status, dns_wire.STATUS_NXDOMAIN)
        self.assertTrue(result.answered)

        empty = self._server(lambda query_id, _t: _response(query_id))
        result = dns_wire.query_server("127.0.0.1", "example.com", dns_wire.TYPE_A, port=empty.port, timeout_s=2)
        self.assertEqual(result.status, dns_wire.STATUS_EMPTY)

    def test_silent_server_is_timeout_not_exception(self) -> None:
        server = self._server(lambda _query_id, _t: None)

        result = dns_wire.query_server("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=0.2, attempts=1)

        self.assertEqual(result.status, dns_wire.STATUS_TIMEOUT)
        self.assertFalse(result.answered)

    def test_foreign_packet_is_ignored(self) -> None:
        def reply(query_id: int, _transport: str) -> bytes:
            return _response((query_id + 1) & 0xFFFF, answers=[_record(1, socket.inet_aton("6.6.6.6"))])

        server = self._server(reply)

        result = dns_wire.query_server("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=0.2, attempts=1)

        self.assertEqual(result.status, dns_wire.STATUS_TIMEOUT)

    def test_truncated_udp_answer_is_retried_over_tcp(self) -> None:
        def reply(query_id: int, transport: str) -> bytes:
            if transport == "udp":
                return _response(query_id, flags=0x8380)
            return _response(query_id, answers=[_record(1, socket.inet_aton("9.9.9.9"))])

        server = self._server(reply)

        result = dns_wire.query_server("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=2)

        self.assertEqual(result.values(dns_wire.TYPE_A), ("9.9.9.9",))
        self.assertEqual(server.transports, ["udp", "tcp"])

    def test_bad_server_address_is_error_status(self) -> None:
        result = dns_wire.query_server("not-an-ip", "example.com", dns_wire.TYPE_A)

        self.assertEqual(result.status, dns_wire.STATUS_ERROR)


if __name__ == "__main__":
    unittest.main()
