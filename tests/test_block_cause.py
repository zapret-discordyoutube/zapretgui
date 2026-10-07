from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from diagnostics import block_cause as bc
from diagnostics.tls_probe import (
    KIND_CONNECT,
    KIND_OK,
    KIND_RESET,
    KIND_TIMEOUT,
    STAGE_CONNECT,
    STAGE_READ,
    STAGE_TLS,
    ProbeResult,
)
from utils.socket_cancel import SocketCancel


def _listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(3)
    return listener


def _serve(listener: socket.socket, handle) -> int:
    def _run() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                handle(connection)
        except OSError:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return listener.getsockname()[1]


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HelloTests(unittest.TestCase):
    """Проба «тот же адрес, другое имя»: чем кончилось начало шифрования."""

    def _port(self, handle) -> int:
        listener = _listener()
        self.addCleanup(listener.close)
        return _serve(listener, handle)

    def _hello(self, port: int, name="example.com", **kwargs) -> bc.HelloResult:
        return bc.tls_hello("127.0.0.1", name, port=port, timeout=kwargs.pop("timeout", 2.0), **kwargs)

    def test_server_refusing_the_name_still_counts_as_answer(self) -> None:
        def refuse(connection) -> None:
            connection.recv(4096)
            # Запись «тревога»: фатальная, «не знаю такого имени» (112).
            connection.sendall(b"\x15\x03\x03\x00\x02\x02\x70")

        result = self._hello(self._port(refuse))

        self.assertEqual(result.kind, bc.HELLO_ALERT)
        self.assertTrue(result.answered)

    def test_connection_dropped_on_hello_is_reset(self) -> None:
        result = self._hello(self._port(lambda connection: connection.recv(4096)))

        self.assertEqual(result.kind, bc.HELLO_RESET)
        self.assertFalse(result.answered)

    def test_hello_silently_swallowed_is_timeout(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def swallow(connection) -> None:
            connection.recv(4096)
            release.wait(3)

        result = self._hello(self._port(swallow), timeout=0.4)

        self.assertEqual(result.kind, bc.HELLO_TIMEOUT)

    def test_reply_that_is_not_encryption_is_garbage_not_answer(self) -> None:
        def garbage(connection) -> None:
            connection.recv(4096)
            connection.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")

        result = self._hello(self._port(garbage))

        self.assertEqual(result.kind, bc.HELLO_GARBAGE)
        self.assertFalse(result.answered)

    def test_closed_port_is_connect_failure(self) -> None:
        self.assertEqual(self._hello(_closed_port()).kind, bc.HELLO_CONNECT)

    def test_cancelled_probe_says_nothing(self) -> None:
        cancel = SocketCancel()
        cancel.cancel()

        self.assertEqual(self._hello(_closed_port(), cancel=cancel).kind, bc.HELLO_CANCELLED)

    @unittest.skipUnless(shutil.which("openssl"), "нужен openssl для учебного сертификата")
    def test_handshake_with_foreign_certificate_is_ok_and_name_is_sent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cert, key = Path(folder) / "cert.pem", Path(folder) / "key.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(key),
                 "-out", str(cert), "-subj", "/CN=other.test"],
                check=True,
                capture_output=True,
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert), str(key))
            names: list = []
            context.sni_callback = lambda _sock, name, _ctx: names.append(name)

            def serve(connection) -> None:
                try:
                    context.wrap_socket(connection, server_side=True).close()
                except (OSError, ssl.SSLError):
                    pass

            # Сертификат чужой и самодельный: проба всё равно «ответил».
            self.assertEqual(self._hello(self._port(serve)).kind, bc.HELLO_OK)
            self.assertEqual(self._hello(self._port(serve), name=None).kind, bc.HELLO_OK)
            self.assertEqual(names, ["example.com", None])


class HttpProbeTests(unittest.TestCase):
    def _probe(self, reply: bytes | None) -> tuple[bc.HttpFacts, list[bytes]]:
        seen: list[bytes] = []
        listener = _listener()
        self.addCleanup(listener.close)

        def handle(connection) -> None:
            seen.append(connection.recv(4096))
            if reply is not None:
                connection.sendall(reply)

        port = _serve(listener, handle)
        return bc.http_probe("blocked.example", "127.0.0.1", port=port, timeout=1.0), seen

    def test_status_location_and_body_are_read(self) -> None:
        facts, seen = self._probe(b"HTTP/1.1 302 Found\r\nLocation: http://warning.rt.ru/?id=1\r\n\r\n<html>stub</html>")

        self.assertEqual((facts.status, facts.location, facts.body), (302, "http://warning.rt.ru/?id=1", b"<html>stub</html>"))
        self.assertIn(b"Host: blocked.example\r\n", seen[0])
        self.assertTrue(seen[0].startswith(b"GET / HTTP/1.1\r\n"))

    def test_no_answer_and_not_http_are_empty_facts(self) -> None:
        self.assertIsNone(self._probe(None)[0].status)
        self.assertIsNone(self._probe(b"SSH-2.0-OpenSSH\r\n")[0].status)
        self.assertIsNone(bc.http_probe("x.example", "127.0.0.1", port=_closed_port(), timeout=1.0).status)


class StubPageTests(unittest.TestCase):
    def test_legal_reasons_code_is_a_block_page(self) -> None:
        self.assertIn("451", bc.stub_reason("site.example", bc.HttpFacts(status=451)))

    def test_redirect_to_operator_page_is_a_block_page(self) -> None:
        facts = bc.HttpFacts(status=302, location="http://block.mts.ru/?host=site.example")

        self.assertIn("block.mts.ru", bc.stub_reason("site.example", facts))

    def test_registry_link_in_page_text_is_a_block_page(self) -> None:
        facts = bc.HttpFacts(status=200, body="<a href='https://EAIS.rkn.gov.ru/'>реестр</a>".encode())

        self.assertTrue(bc.stub_reason("site.example", facts))

    def test_ordinary_answers_are_not_block_pages(self) -> None:
        ordinary = (
            bc.HttpFacts(status=301, location="https://site.example/"),
            bc.HttpFacts(status=301, location="https://www.site.example/"),
            # Переход на чужой сайт сам по себе — не блокировка: так делают и обычные сайты.
            bc.HttpFacts(status=302, location="https://login.other.example/"),
            # Защита от ботов и обычные слова на странице.
            bc.HttpFacts(status=403, body="Доступ заблокирован: вы робот?".encode()),
            bc.HttpFacts(status=429),
            bc.HttpFacts(status=200, body=b"<html>hello</html>"),
            bc.HttpFacts(),
            None,
        )
        for facts in ordinary:
            with self.subTest(facts=facts):
                self.assertEqual(bc.stub_reason("site.example", facts), "")

    def test_site_about_the_registry_itself_is_not_its_own_block_page(self) -> None:
        facts = bc.HttpFacts(status=301, location="https://rkn.gov.ru/")

        self.assertEqual(bc.stub_reason("rkn.gov.ru", facts), "")


def _broken(kind=KIND_RESET, stage=STAGE_TLS) -> ProbeResult:
    return ProbeResult(ip="203.0.113.5", kind=kind, stage=stage)


def _facts(result=None, **extra) -> bc.CauseFacts:
    return bc.CauseFacts(host="discord.com", result=result or _broken(), **extra)


ANSWER = bc.HelloResult(bc.HELLO_OK)
REFUSAL = bc.HelloResult(bc.HELLO_ALERT)
RESET = bc.HelloResult(bc.HELLO_RESET)
SILENCE = bc.HelloResult(bc.HELLO_TIMEOUT)


class JudgeTests(unittest.TestCase):
    def test_other_name_answers_means_block_by_name(self) -> None:
        for neutral, nameless in ((ANSWER, RESET), (REFUSAL, RESET), (RESET, ANSWER)):
            with self.subTest(neutral=neutral.kind, nameless=nameless.kind):
                cause = bc.judge(_facts(neutral=neutral, nameless=nameless))
                self.assertEqual((cause.code, cause.confident), (bc.CAUSE_BY_NAME, True))
                self.assertIn("discord.com", cause.text)

    def test_text_says_which_probe_answered_and_how_real_one_failed(self) -> None:
        by_other = bc.judge(_facts(neutral=ANSWER, nameless=RESET)).text
        self.assertIn("обрывается", by_other)
        self.assertIn("с посторонним именем", by_other)

        without = bc.judge(_facts(_broken(KIND_TIMEOUT), neutral=SILENCE, nameless=ANSWER)).text
        self.assertIn("молча пропадает", without)
        self.assertIn("без имени", without)

    def test_no_name_works_means_address_itself_is_closed_but_not_for_sure(self) -> None:
        cause = bc.judge(_facts(neutral=RESET, nameless=SILENCE))

        self.assertEqual((cause.code, cause.confident), (bc.CAUSE_BY_ADDRESS, False))

    def test_no_conclusion_from_unclear_or_missing_probes(self) -> None:
        unclear = (
            _facts(),
            _facts(neutral=bc.HelloResult(bc.HELLO_CANCELLED), nameless=bc.HelloResult(bc.HELLO_CANCELLED)),
            # Мусор вместо шифрования — ответил не сервер: ни «по имени», ни «по адресу».
            _facts(neutral=bc.HelloResult(bc.HELLO_GARBAGE), nameless=RESET),
            _facts(neutral=bc.HelloResult(bc.HELLO_CONNECT), nameless=RESET),
        )
        for facts in unclear:
            with self.subTest(facts=facts):
                self.assertIsNone(bc.judge(facts))

    def test_block_page_wins_over_everything(self) -> None:
        cause = bc.judge(_facts(neutral=ANSWER, nameless=ANSWER, http=bc.HttpFacts(status=451)))

        self.assertEqual((cause.code, cause.confident), (bc.CAUSE_STUB_PAGE, True))

    def test_address_that_pings_but_refuses_connections_is_closed(self) -> None:
        refused = _broken(KIND_CONNECT, STAGE_CONNECT)

        self.assertEqual(bc.judge(_facts(refused, ping_ok=True)).code, bc.CAUSE_ADDRESS_CLOSED)
        self.assertEqual(bc.judge(_facts(refused, ping_ok=False)).code, bc.CAUSE_ADDRESS_SILENT)
        # Пинга в системе нет — сказать нечего.
        self.assertIsNone(bc.judge(_facts(refused, ping_ok=None)))


class RefiningScopeTests(unittest.TestCase):
    def test_only_failures_before_encryption_is_up_are_refined(self) -> None:
        self.assertTrue(bc.needs_refining(_broken(KIND_RESET, STAGE_TLS)))
        self.assertTrue(bc.needs_refining(_broken(KIND_TIMEOUT, STAGE_TLS)))
        self.assertTrue(bc.needs_refining(_broken(KIND_CONNECT, STAGE_CONNECT)))
        # Шифрование установилось — имя уже пропустили, уточнять нечего.
        self.assertFalse(bc.needs_refining(_broken(KIND_RESET, STAGE_READ)))
        self.assertFalse(bc.needs_refining(ProbeResult(ip="203.0.113.5", kind=KIND_OK)))
        self.assertFalse(bc.needs_refining(ProbeResult(ip="", kind=KIND_RESET, stage=STAGE_TLS)))
        self.assertFalse(bc.needs_refining(None))


class CollectTests(unittest.TestCase):
    def _collect(self, result, **patches):
        from unittest.mock import patch

        calls: list[tuple] = []

        def hello(ip, name, **_kwargs):
            calls.append(("hello", ip, name))
            return ANSWER

        def http(host, ip, **_kwargs):
            calls.append(("http", host, ip))
            return bc.HttpFacts(status=200)

        with ThreadPoolExecutor(4) as pool, patch.object(bc, "tls_hello", hello), patch.object(bc, "http_probe", http):
            facts = bc.collect("discord.com", result, submit=pool.submit, cancel=SocketCancel(), **patches)
        return facts, calls

    def test_failed_encryption_is_retried_with_other_name_and_without_name(self) -> None:
        facts, calls = self._collect(_broken())

        self.assertEqual(
            sorted(calls, key=repr),
            sorted(
                [("hello", "203.0.113.5", bc.NEUTRAL_NAME), ("hello", "203.0.113.5", None), ("http", "discord.com", "203.0.113.5")],
                key=repr,
            ),
        )
        self.assertEqual((facts.neutral, facts.nameless, facts.http.status), (ANSWER, ANSWER, 200))

    def test_refused_connection_is_pinged_instead(self) -> None:
        pinged: list[str] = []

        def ping(ip):
            pinged.append(ip)
            return True

        facts, calls = self._collect(_broken(KIND_CONNECT, STAGE_CONNECT), ping=ping)

        self.assertEqual(pinged, ["203.0.113.5"])
        self.assertTrue(facts.ping_ok)
        self.assertEqual([call[0] for call in calls], ["http"])
        self.assertIsNone(facts.neutral)


if __name__ == "__main__":
    unittest.main()
