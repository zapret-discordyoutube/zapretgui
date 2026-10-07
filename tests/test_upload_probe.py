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
from unittest.mock import patch

from diagnostics import upload_probe as up
from diagnostics.freeze_check import (
    DIRECTION_DOWNLOAD,
    DIRECTION_UPLOAD,
    FreezeServer,
    FreezeState,
    _check_provider,
    summarize_freeze,
)
from diagnostics.tls_probe import KIND_OK, ProbeResult
from diagnostics.verdict import Level
from utils.socket_cancel import SocketCancel

HOST = "upload.test"


def _answered(status: int = 405, sent: int = 0) -> up.PostResult:
    return up.PostResult(up.POST_ANSWERED, sent, status)


STALLED = up.PostResult(up.POST_STALLED, 20_000)
FAILED = up.PostResult(up.POST_FAILED, 4096)


class JudgeTests(unittest.TestCase):
    def test_everything_answered_is_fine(self) -> None:
        verdict = up.judge(up.UploadFacts(_answered(), _answered(), _answered()))

        self.assertEqual(verdict.code, up.UPLOAD_OK)

    def test_big_upload_stalls_while_small_passes_is_volume_cut(self) -> None:
        verdict = up.judge(up.UploadFacts(_answered(), STALLED, _answered()))

        self.assertEqual(verdict.code, up.UPLOAD_BULK_STALLS)
        self.assertIn("64 КБ", verdict.text)
        self.assertIn("короткую отправку тот же сервер принимает", verdict.text)

    def test_only_small_packets_stall_is_packet_limit(self) -> None:
        verdict = up.judge(up.UploadFacts(_answered(), _answered(), STALLED))

        self.assertEqual(verdict.code, up.UPLOAD_PACKET_LIMIT)
        self.assertIn("32 мелких пакетах", verdict.text)

    def test_volume_cut_is_named_first_when_both_stall(self) -> None:
        self.assertEqual(up.judge(up.UploadFacts(_answered(), STALLED, STALLED)).code, up.UPLOAD_BULK_STALLS)

    def test_no_verdict_without_working_control(self) -> None:
        """Короткая отправка не прошла — сравнивать не с чем, блокировкой это не называется."""
        for small in (STALLED, FAILED, up.PostResult(up.POST_EARLY, 0, 403)):
            with self.subTest(small=small.kind):
                self.assertEqual(up.judge(up.UploadFacts(small)).code, up.UPLOAD_UNKNOWN)
        self.assertIn("не дочитав", up.judge(up.UploadFacts(up.PostResult(up.POST_EARLY, 0, 403))).text)

    def test_server_dropping_upload_itself_is_not_a_stall(self) -> None:
        self.assertEqual(up.judge(up.UploadFacts(_answered(), FAILED, _answered())).code, up.UPLOAD_UNKNOWN)
        self.assertEqual(up.judge(up.UploadFacts(_answered(), _answered(), FAILED)).code, up.UPLOAD_UNKNOWN)

    def test_interrupted_probe_gives_no_verdict(self) -> None:
        cancelled = up.PostResult(up.POST_CANCELLED)

        self.assertEqual(up.judge(up.UploadFacts(cancelled)).code, up.UPLOAD_UNKNOWN)
        self.assertEqual(up.judge(up.UploadFacts(_answered(), cancelled, STALLED)).code, up.UPLOAD_UNKNOWN)


class CollectTests(unittest.TestCase):
    def _collect(self, small: up.PostResult):
        calls: list[dict] = []

        def fake_post(host, ip, path, chunks, **kwargs):
            chunks = list(chunks)
            calls.append({"size": sum(map(len, chunks)), "count": len(chunks), **kwargs})
            return small if len(calls) == 1 else _answered()

        with ThreadPoolExecutor(2) as pool, patch.object(up, "post", fake_post):
            facts = up.collect(HOST, "203.0.113.5", "/file", submit=pool.submit, cancel=SocketCancel())
        return facts, calls

    def _collect_bulk(self, answers):
        """Большая отправка отвечает по очереди ``answers``; остальные проходят."""
        bulk_calls = []

        def fake_post(host, ip, path, chunks, **kwargs):
            if sum(map(len, chunks)) != up.BULK_BYTES:
                return _answered()
            bulk_calls.append(1)
            return answers[min(len(bulk_calls), len(answers)) - 1]

        with ThreadPoolExecutor(2) as pool, patch.object(up, "post", fake_post):
            facts = up.collect(HOST, "203.0.113.5", "/file", submit=pool.submit, cancel=SocketCancel())
        return facts, len(bulk_calls)

    def test_single_stall_is_rechecked_and_does_not_count(self) -> None:
        facts, calls = self._collect_bulk([STALLED, _answered()])

        self.assertEqual((calls, up.judge(facts).code), (2, up.UPLOAD_OK))

    def test_stall_twice_in_a_row_is_the_finding(self) -> None:
        facts, calls = self._collect_bulk([STALLED, STALLED])

        self.assertEqual((calls, up.judge(facts).code), (2, up.UPLOAD_BULK_STALLS))
        self.assertIn("дважды подряд", up.judge(facts).text)

    def test_passing_upload_is_sent_once(self) -> None:
        self.assertEqual(self._collect_bulk([_answered()])[1], 1)

    def test_control_goes_first_and_checks_that_server_waits_for_body(self) -> None:
        facts, calls = self._collect(_answered())

        self.assertEqual((calls[0]["size"], calls[0]["count"]), (16, 1))
        self.assertGreater(calls[0]["early_wait"], 0)
        rest = sorted(calls[1:], key=lambda call: call["size"])
        # Мелкими пакетами: 64 байта, 32 куска, с паузой. Большая: 64 КБ.
        self.assertEqual((rest[0]["size"], rest[0]["count"]), (64, 32))
        self.assertGreater(rest[0]["pause"], 0)
        self.assertEqual(rest[1]["size"], 64 * 1024)
        self.assertIsNotNone(facts.bulk)
        self.assertIsNotNone(facts.drip)

    def test_nothing_more_is_sent_when_control_fails(self) -> None:
        facts, calls = self._collect(STALLED)

        self.assertEqual(len(calls), 1)
        self.assertIsNone(facts.bulk)
        self.assertIsNone(facts.drip)


@unittest.skipUnless(shutil.which("openssl"), "нужен openssl для учебного сертификата")
class PostTests(unittest.TestCase):
    """Настоящая отправка на учебный сервер с шифрованием."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._folder = tempfile.TemporaryDirectory()
        folder = Path(cls._folder.name)
        cls.cert, cls.key = folder / "cert.pem", folder / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(cls.key),
             "-out", str(cls.cert), "-subj", f"/CN={HOST}", "-addext", f"subjectAltName=DNS:{HOST}"],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._folder.cleanup()

    def _serve(self, handle) -> int:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)
        self.addCleanup(listener.close)

        def _run() -> None:
            try:
                connection, _address = listener.accept()
                with context.wrap_socket(connection, server_side=True) as tls:
                    handle(tls)
            except (OSError, ssl.SSLError):
                pass

        threading.Thread(target=_run, daemon=True).start()
        return listener.getsockname()[1]

    def _post(self, port: int, chunks, **kwargs) -> up.PostResult:
        trusting = ssl.create_default_context(cafile=str(self.cert))
        with patch.object(up, "_client_context", return_value=trusting):
            return up.post(HOST, "127.0.0.1", "/upload", chunks, port=port, timeout=kwargs.pop("timeout", 2.0), **kwargs)

    @staticmethod
    def _read_request(tls) -> tuple[bytes, bytes]:
        data = b""
        while b"\r\n\r\n" not in data:
            data += tls.recv(65536)
        head, _sep, body = data.partition(b"\r\n\r\n")
        length = int(next(line.split(b":")[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length")))
        while len(body) < length:
            body += tls.recv(65536)
        return head, body

    def test_whole_body_reaches_server_and_answer_is_read(self) -> None:
        seen: dict = {}

        def handle(tls) -> None:
            seen["head"], seen["body"] = self._read_request(tls)
            tls.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")

        body = bytes(range(256)) * 64
        result = self._post(self._serve(handle), [body[i : i + 4096] for i in range(0, len(body), 4096)])

        self.assertEqual((result.kind, result.status, result.sent), (up.POST_ANSWERED, 405, len(body)))
        self.assertEqual(seen["body"], body)
        self.assertTrue(seen["head"].startswith(b"POST /upload HTTP/1.1\r\n"))
        self.assertIn(f"Content-Length: {len(body)}".encode(), seen["head"])

    def test_server_that_stops_answering_is_stalled(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def handle(tls) -> None:
            tls.recv(1024)
            release.wait(3)

        result = self._post(self._serve(handle), [b"x" * 16], timeout=0.4)

        self.assertEqual(result.kind, up.POST_STALLED)

    def test_answer_before_body_is_early(self) -> None:
        def handle(tls) -> None:
            tls.recv(1024)
            tls.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")

        result = self._post(self._serve(handle), [b"x" * 16], early_wait=1.0)

        self.assertEqual((result.kind, result.status, result.sent), (up.POST_EARLY, 403, 0))

    def test_server_waiting_for_body_passes_the_early_check(self) -> None:
        def handle(tls) -> None:
            self._read_request(tls)
            tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

        result = self._post(self._serve(handle), [b"x" * 16], early_wait=0.2)

        self.assertEqual((result.kind, result.status), (up.POST_ANSWERED, 200))

    def test_small_chunks_arrive_with_pauses(self) -> None:
        def handle(tls) -> None:
            self._read_request(tls)
            tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

        result = self._post(self._serve(handle), [b"ab"] * 8, pause=0.01)

        self.assertEqual((result.kind, result.sent), (up.POST_ANSWERED, 16))

    def test_closed_port_and_cancel_are_not_stalls(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertEqual(self._post(port, [b"x"]).kind, up.POST_FAILED)

        cancel = SocketCancel()
        cancel.cancel()
        self.assertEqual(self._post(port, [b"x"], cancel=cancel).kind, up.POST_CANCELLED)


class FreezeCheckUploadTests(unittest.TestCase):
    """Отправка проверяется на том же сервере, но только если загрузка прошла."""

    CANDIDATES = [{"url": "https://cdn.example/file.bin", "provider": "Akamai"}]

    @staticmethod
    def _download(size: int, cut: bool = False):
        return lambda host, path: ProbeResult(ip="203.0.113.5", kind=KIND_OK, status=200, body_size=size, body_cut=cut)

    def _check(self, download, upload):
        asked: list[tuple[str, str]] = []

        def spy(host, path):
            asked.append((host, path))
            return upload

        return _check_provider("Akamai", self.CANDIDATES, download, lambda: True, spy), asked

    def test_stalling_upload_turns_clean_download_into_freeze(self) -> None:
        verdict = up.UploadVerdict(up.UPLOAD_BULK_STALLS, "отправка 64 КБ замирает")
        server, asked = self._check(self._download(32 * 1024), verdict)

        self.assertEqual((server.state, server.direction), (FreezeState.FREEZE, DIRECTION_UPLOAD))
        self.assertEqual(server.text, "загрузка проходит, но отправка 64 КБ замирает")
        self.assertEqual(asked, [("cdn.example", "/file.bin")])

    def test_working_upload_is_noted_and_unknown_changes_nothing(self) -> None:
        fine, _ = self._check(self._download(32 * 1024), up.UploadVerdict(up.UPLOAD_OK, "проходит"))
        self.assertEqual(fine.state, FreezeState.OK)
        self.assertTrue(fine.text.endswith("; отправка тоже проходит"))

        unknown, _ = self._check(self._download(32 * 1024), up.UploadVerdict(up.UPLOAD_UNKNOWN, "не на чем"))
        self.assertEqual((unknown.state, unknown.text), (FreezeState.OK, "получено 32 КБ без обрыва"))

        none, _ = self._check(self._download(32 * 1024), None)
        self.assertEqual(none.state, FreezeState.OK)

    def test_upload_is_not_checked_when_download_already_failed(self) -> None:
        frozen, asked = self._check(self._download(18 * 1024, cut=True), up.UploadVerdict(up.UPLOAD_OK, "проходит"))

        self.assertEqual((frozen.state, frozen.direction), (FreezeState.FREEZE, DIRECTION_DOWNLOAD))
        self.assertEqual(asked, [])

    def test_headline_names_the_direction(self) -> None:
        ok = FreezeServer("A", FreezeState.OK, "ок")
        down = FreezeServer("B", FreezeState.FREEZE, "обрыв", DIRECTION_DOWNLOAD)
        upload = FreezeServer("C", FreezeState.FREEZE, "замирает", DIRECTION_UPLOAD)

        self.assertIn(
            "Обрывается загрузка с зарубежных серверов на 16–20 КБ: 1 из 2",
            summarize_freeze((ok, down), zapret_running=True).headline,
        )
        only_upload = summarize_freeze((ok, ok, upload), zapret_running=True)
        self.assertIn("Обрывается отправка данных на зарубежные серверы: 1 из 3", only_upload.headline)
        self.assertEqual(only_upload.level, Level.WARN)
        self.assertIn("и отправка данных на них", summarize_freeze((down, upload), zapret_running=True).headline)

    def test_one_cut_among_many_is_a_single_case_not_a_verdict(self) -> None:
        ok = FreezeServer("A", FreezeState.OK, "ок")
        down = FreezeServer("B", FreezeState.FREEZE, "обрыв", DIRECTION_DOWNLOAD)
        unknown = FreezeServer("U", FreezeState.UNKNOWN, "не ответил")

        single = summarize_freeze((ok,) * 20 + (down,), zapret_running=True)
        self.assertEqual(single.level, Level.OK)
        self.assertIn("единичный случай", single.headline)
        # Один обрыв, один «без обрыва» и толпа неизвестных — это не «провайдер обрывает».
        thin = summarize_freeze((down, ok) + (unknown,) * 57, zapret_running=True)
        self.assertEqual(thin.level, Level.WARN)
        self.assertIn("1 из 2 проверенных", thin.headline)

    def test_failure_needs_several_servers_and_a_real_share(self) -> None:
        ok = FreezeServer("A", FreezeState.OK, "ок")
        down = FreezeServer("B", FreezeState.FREEZE, "обрыв", DIRECTION_DOWNLOAD)

        self.assertEqual(summarize_freeze((down,) * 2 + (ok,) * 2, zapret_running=True).level, Level.WARN)
        self.assertEqual(summarize_freeze((down,) * 3 + (ok,) * 40, zapret_running=True).level, Level.WARN)
        self.assertEqual(summarize_freeze((down,) * 20 + (ok,) * 30, zapret_running=True).level, Level.FAIL)
        self.assertEqual(summarize_freeze((down,) * 3 + (ok,) * 3, zapret_running=True).level, Level.FAIL)

    def test_dead_server_is_not_described_as_traffic_interception(self) -> None:
        from diagnostics.freeze_check import classify_download
        from diagnostics.tls_probe import ProbeResult

        state, text = classify_download(ProbeResult(ip="1.2.3.4", kind="cert", cert_problem="просрочен"))

        self.assertEqual(state, FreezeState.UNKNOWN)
        self.assertNotIn("перехват", text)
        self.assertNotIn("антивирус", text)
        self.assertIn("мог переехать", text)


if __name__ == "__main__":
    unittest.main()
