from __future__ import annotations

import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import dns_wire
from utils.socket_cancel import SocketCancel


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



class SeparateTransportTests(unittest.TestCase):
    """UDP и TCP проверяются порознь: провайдер может закрыть один из них."""

    def _server(self, reply) -> _FakeDnsServer:
        server = _FakeDnsServer(reply)
        self.addCleanup(server.close)
        return server

    def test_udp_reports_truncation_and_does_not_switch_to_tcp(self) -> None:
        server = self._server(lambda query_id, _t: _response(query_id, flags=0x8380))

        result = dns_wire.query_udp("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=2)

        self.assertTrue(result.answered)
        self.assertTrue(result.truncated)
        self.assertEqual(result.transport, dns_wire.TRANSPORT_UDP)
        self.assertEqual(server.transports, ["udp"])

    def test_tcp_answers_without_touching_udp(self) -> None:
        server = self._server(lambda query_id, _t: _response(query_id, answers=[_record(1, socket.inet_aton("9.9.9.9"))]))

        result = dns_wire.query_tcp("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=2)

        self.assertEqual(result.values(dns_wire.TYPE_A), ("9.9.9.9",))
        self.assertEqual(result.transport, dns_wire.TRANSPORT_TCP)
        self.assertEqual(server.transports, ["tcp"])

    def test_closed_tcp_port_is_refused_not_timeout(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        result = dns_wire.query_tcp("127.0.0.1", "example.com", dns_wire.TYPE_A, port=port, timeout_s=2)

        self.assertEqual(result.status, dns_wire.STATUS_ERROR)
        self.assertEqual(result.failure, dns_wire.FAILURE_REFUSED)

    def test_tcp_server_that_hangs_up_is_closed_not_timeout(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def _serve() -> None:
            connection, _address = listener.accept()
            connection.recv(4096)
            connection.close()

        threading.Thread(target=_serve, daemon=True).start()

        result = dns_wire.query_tcp(
            "127.0.0.1", "example.com", dns_wire.TYPE_A, port=listener.getsockname()[1], timeout_s=2
        )

        self.assertEqual(result.failure, dns_wire.FAILURE_CLOSED)

    def test_silent_tcp_server_is_timeout(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        result = dns_wire.query_tcp(
            "127.0.0.1", "example.com", dns_wire.TYPE_A, port=listener.getsockname()[1], timeout_s=0.3
        )

        self.assertEqual(result.status, dns_wire.STATUS_TIMEOUT)
        self.assertEqual(result.failure, dns_wire.FAILURE_TIMEOUT)

    def test_tcp_answer_to_another_query_is_malformed(self) -> None:
        server = self._server(
            lambda query_id, _t: _response((query_id + 1) & 0xFFFF, answers=[_record(1, socket.inet_aton("6.6.6.6"))])
        )

        result = dns_wire.query_tcp("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=2)

        self.assertFalse(result.answered)
        self.assertEqual(result.failure, dns_wire.FAILURE_MALFORMED)

    def test_cancel_stops_a_waiting_query_at_once(self) -> None:
        server = self._server(lambda _query_id, _t: None)
        cancel = SocketCancel()
        threading.Timer(0.2, cancel.cancel).start()

        result = dns_wire.query_udp(
            "127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port, timeout_s=10, cancel=cancel
        )

        self.assertEqual(result.failure, dns_wire.FAILURE_CANCELLED)

    def test_cancelled_token_does_not_start_a_query(self) -> None:
        cancel = SocketCancel()
        cancel.cancel()

        for query in (dns_wire.query_udp, dns_wire.query_tcp, dns_wire.query_dot, dns_wire.query_doh):
            with self.subTest(query=query.__name__):
                result = query("127.0.0.1", "example.com", dns_wire.TYPE_A, port=9, cancel=cancel)
                self.assertEqual(result.failure, dns_wire.FAILURE_CANCELLED)


TLS_NAME = "dns.test"


def _make_certificate(folder: Path) -> tuple[Path, Path]:
    """Самоподписанный сертификат на имя ``dns.test`` и на адрес 127.0.0.1."""
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
            "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={TLS_NAME}",
            "-addext", f"subjectAltName=DNS:{TLS_NAME},IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


class _TlsServer:
    """Сервер на localhost с шифрованием: одно соединение отдаётся в ``handle``."""

    def __init__(self, cert: Path, key: Path, handle) -> None:
        self._handle = handle
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(str(cert), str(key))
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(3)
        self.port = self._listener.getsockname()[1]
        self.server_names: list[str | None] = []
        self._context.sni_callback = lambda _sock, name, _ctx: self.server_names.append(name)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            connection, _address = self._listener.accept()
            with self._context.wrap_socket(connection, server_side=True) as tls:
                self._handle(tls)
        except (OSError, ssl.SSLError):
            pass

    def close(self) -> None:
        self._listener.close()


def _read_framed(tls) -> bytes:
    size = struct.unpack("!H", tls.recv(2))[0]
    return tls.recv(size)


def _read_http_request(tls) -> tuple[str, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        data += tls.recv(4096)
    head, _, body = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value)
    while len(body) < length:
        body += tls.recv(4096)
    return head.decode("ascii"), body


@unittest.skipUnless(shutil.which("openssl"), "нужен openssl для учебного сертификата")
class EncryptedTransportTests(unittest.TestCase):
    """DoT и DoH на учебном сервере: настоящее шифрование, но без выхода в сеть."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._folder = tempfile.TemporaryDirectory()
        cls.cert, cls.key = _make_certificate(Path(cls._folder.name))
        cls.trusting = ssl.create_default_context(cafile=str(cls.cert))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._folder.cleanup()

    def _server(self, handle) -> _TlsServer:
        server = _TlsServer(self.cert, self.key, handle)
        self.addCleanup(server.close)
        return server

    def _trust(self):
        return patch.object(dns_wire, "_client_tls_context", return_value=self.trusting)

    @staticmethod
    def _answer(query: bytes, address: str = "5.5.5.5") -> bytes:
        return _response(struct.unpack("!H", query[:2])[0], answers=[_record(1, socket.inet_aton(address))])

    def _dot_handler(self, tls) -> None:
        answer = self._answer(_read_framed(tls))
        tls.sendall(struct.pack("!H", len(answer)) + answer)

    def test_dot_answers_and_checks_certificate_for_given_name(self) -> None:
        server = self._server(self._dot_handler)

        with self._trust():
            result = dns_wire.query_dot(
                "127.0.0.1", "example.com", dns_wire.TYPE_A, tls_host=TLS_NAME, port=server.port
            )

        self.assertEqual(result.values(dns_wire.TYPE_A), ("5.5.5.5",))
        self.assertEqual(result.transport, dns_wire.TRANSPORT_DOT)
        self.assertEqual(server.server_names, [TLS_NAME])

    def test_dot_without_name_checks_certificate_by_address(self) -> None:
        server = self._server(self._dot_handler)

        with self._trust():
            result = dns_wire.query_dot("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port)

        self.assertTrue(result.answered)

    def test_dot_with_wrong_name_is_certificate_failure(self) -> None:
        server = self._server(self._dot_handler)

        with self._trust():
            result = dns_wire.query_dot(
                "127.0.0.1", "example.com", dns_wire.TYPE_A, tls_host="dns.google", port=server.port
            )

        self.assertFalse(result.answered)
        self.assertEqual(result.failure, dns_wire.FAILURE_CERT)

    def test_dot_with_untrusted_certificate_is_certificate_failure(self) -> None:
        server = self._server(self._dot_handler)

        result = dns_wire.query_dot(
            "127.0.0.1", "example.com", dns_wire.TYPE_A, tls_host=TLS_NAME, port=server.port
        )

        self.assertEqual(result.failure, dns_wire.FAILURE_CERT)

    def test_dot_on_plain_port_is_tls_failure(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def _serve() -> None:
            connection, _address = listener.accept()
            connection.recv(4096)
            connection.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            connection.close()

        threading.Thread(target=_serve, daemon=True).start()

        result = dns_wire.query_dot(
            "127.0.0.1", "example.com", dns_wire.TYPE_A, tls_host=TLS_NAME, port=listener.getsockname()[1]
        )

        self.assertEqual(result.failure, dns_wire.FAILURE_TLS)

    def test_doh_posts_dns_message_with_host_name_and_port(self) -> None:
        seen: list[str] = []

        def handle(tls) -> None:
            head, body = _read_http_request(tls)
            seen.append(head)
            answer = self._answer(body, "7.7.7.7")
            tls.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/dns-message\r\n"
                + f"Content-Length: {len(answer)}\r\n\r\n".encode("ascii")
                + answer
            )

        server = self._server(handle)

        with self._trust():
            result = dns_wire.query_doh(
                "127.0.0.1", "example.com", dns_wire.TYPE_A, tls_host=TLS_NAME, port=server.port, path="/q"
            )

        self.assertEqual(result.values(dns_wire.TYPE_A), ("7.7.7.7",))
        self.assertEqual(result.transport, dns_wire.TRANSPORT_DOH)
        self.assertTrue(seen[0].startswith("POST /q HTTP/1.1"))
        self.assertIn(f"Host: {TLS_NAME}:{server.port}", seen[0])
        self.assertIn("Content-Type: application/dns-message", seen[0])

    def test_doh_reads_chunked_answer(self) -> None:
        def handle(tls) -> None:
            _head, body = _read_http_request(tls)
            answer = self._answer(body, "8.8.4.4")
            tls.sendall(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                + f"{len(answer):x}\r\n".encode("ascii")
                + answer
                + b"\r\n0\r\n\r\n"
            )

        server = self._server(handle)

        with self._trust():
            result = dns_wire.query_doh("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port)

        self.assertEqual(result.values(dns_wire.TYPE_A), ("8.8.4.4",))

    def test_doh_http_error_is_its_own_failure(self) -> None:
        def handle(tls) -> None:
            _read_http_request(tls)
            tls.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")

        server = self._server(handle)

        with self._trust():
            result = dns_wire.query_doh("127.0.0.1", "example.com", dns_wire.TYPE_A, port=server.port)

        self.assertFalse(result.answered)
        self.assertEqual(result.failure, dns_wire.FAILURE_HTTP)
        self.assertEqual(result.detail, "HTTP 403")

    def test_doh_closed_port_is_refused(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        result = dns_wire.query_doh("127.0.0.1", "example.com", dns_wire.TYPE_A, port=port)

        self.assertEqual(result.failure, dns_wire.FAILURE_REFUSED)


if __name__ == "__main__":
    unittest.main()
