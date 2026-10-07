from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from diagnostics import volume_probe as vp
from utils.socket_cancel import SocketCancel

HOST = "volume.test"


def _run(kind: str, received: int = 0, requests: int = 1) -> vp.VolumeRun:
    return vp.VolumeRun(kind, received, requests)


PASSED = _run(vp.RUN_PASSED, 49_500, 4)


class JudgeTests(unittest.TestCase):
    def test_enough_volume_means_no_cut(self) -> None:
        verdict = vp.judge(vp.VolumeFacts(PASSED))

        self.assertEqual(verdict.code, vp.VOLUME_OK)
        self.assertIn("48 КБ без обрыва", verdict.text)

    def test_cut_needs_two_stalls_in_the_window(self) -> None:
        verdict = vp.judge(vp.VolumeFacts(_run(vp.RUN_STALLED, 14_500, 20), _run(vp.RUN_STALLED, 13_800, 41)))

        self.assertEqual(verdict.code, vp.VOLUME_CUT)
        self.assertEqual(verdict.cut_kb, 14)
        self.assertIn("дважды подряд, на 14 и 13 КБ", verdict.text)

    def test_reset_in_the_window_counts_like_a_stall(self) -> None:
        verdict = vp.judge(vp.VolumeFacts(_run(vp.RUN_RESET, 16_000), _run(vp.RUN_STALLED, 15_000)))

        self.assertEqual(verdict.code, vp.VOLUME_CUT)

    def test_single_stall_is_not_a_verdict(self) -> None:
        """Один раз замерло, а при повторе прошло или упало иначе — случайность, а не блокировка."""
        stalled = _run(vp.RUN_STALLED, 14_500)
        for second in (PASSED, _run(vp.RUN_CLOSED, 3_000), _run(vp.RUN_STALLED, 2_000), None):
            with self.subTest(second=second):
                self.assertEqual(vp.judge(vp.VolumeFacts(stalled, second)).code, vp.VOLUME_UNKNOWN)
        self.assertIn("случайный сбой", vp.judge(vp.VolumeFacts(stalled, PASSED)).text)

    def test_window_passed_without_stall_means_no_cut(self) -> None:
        """Запросы кончились раньше 48 КБ, но окно обрыва пройдено — обрыва нет."""
        verdict = vp.judge(vp.VolumeFacts(_run(vp.RUN_CLOSED, 40_000, 60)))

        self.assertEqual(verdict.code, vp.VOLUME_OK)

    def test_stall_outside_the_window_is_not_a_cut(self) -> None:
        for received in (0, 2_000, 37_000):
            with self.subTest(received=received):
                verdict = vp.judge(vp.VolumeFacts(_run(vp.RUN_STALLED, received)))
                self.assertEqual(verdict.code, vp.VOLUME_UNKNOWN)

    def test_server_that_gives_too_little_cannot_be_checked(self) -> None:
        verdict = vp.judge(vp.VolumeFacts(_run(vp.RUN_CLOSED, 4_000, 60)))

        self.assertEqual(verdict.code, vp.VOLUME_UNKNOWN)
        self.assertIn("не проверить", verdict.text)

    def test_interrupted_probe_gives_no_verdict(self) -> None:
        cancelled = _run(vp.RUN_CANCELLED)

        self.assertEqual(vp.judge(vp.VolumeFacts(cancelled)).code, vp.VOLUME_UNKNOWN)
        self.assertEqual(vp.judge(vp.VolumeFacts(_run(vp.RUN_STALLED, 14_000), cancelled)).code, vp.VOLUME_UNKNOWN)


class CollectTests(unittest.TestCase):
    def _collect(self, *runs: vp.VolumeRun):
        calls: list[str] = []
        queue = list(runs)

        def download(_host, _ip, _path="/", *, method="GET", **_kwargs):
            calls.append(method)
            return queue.pop(0)

        with patch.object(vp, "download", download):
            return vp.collect(HOST, "127.0.0.1", "/", cancel=SocketCancel()), calls

    def test_second_attempt_only_after_a_stall_in_the_window(self) -> None:
        facts, calls = self._collect(PASSED)
        self.assertEqual((calls, facts.second), (["GET"], None))

        facts, calls = self._collect(_run(vp.RUN_CLOSED, 3_000))
        self.assertEqual((calls, facts.second), (["GET"], None))

    def test_stall_is_rechecked_with_another_kind_of_request(self) -> None:
        """Повтор идёт запросами HEAD: до того же объёма нужно другое число запросов."""
        second = _run(vp.RUN_STALLED, 14_000, 40)
        facts, calls = self._collect(_run(vp.RUN_STALLED, 15_000, 20), second)

        self.assertEqual(calls, ["GET", "HEAD"])
        self.assertIs(facts.second, second)


@unittest.skipUnless(shutil.which("openssl"), "нужен openssl для учебного сертификата")
class DownloadTests(unittest.TestCase):
    """Настоящий набор объёма на учебном сервере с шифрованием."""

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

        def _run_server() -> None:
            try:
                connection, _address = listener.accept()
                with context.wrap_socket(connection, server_side=True) as tls:
                    handle(tls)
            except (OSError, ssl.SSLError):
                pass

        threading.Thread(target=_run_server, daemon=True).start()
        return listener.getsockname()[1]

    def _download(self, port: int, **kwargs) -> vp.VolumeRun:
        trusting = ssl.create_default_context(cafile=str(self.cert))
        with patch.object(vp, "_client_context", return_value=trusting):
            return vp.download(HOST, "127.0.0.1", "/page", port=port, timeout=2.0, **kwargs)

    @staticmethod
    def _requests(tls):
        """Отдаёт запросы по одному, пока клиент их шлёт."""
        data = b""
        while True:
            while b"\r\n\r\n" not in data:
                chunk = tls.recv(65536)
                if not chunk:
                    return
                data += chunk
            head, _sep, data = data.partition(b"\r\n\r\n")
            yield head

    def test_many_small_answers_add_up_on_one_connection(self) -> None:
        seen: list[bytes] = []

        def handle(tls) -> None:
            for head in self._requests(tls):
                seen.append(head)
                tls.sendall(b"HTTP/1.1 301 Moved\r\nContent-Length: 900\r\n\r\n" + b"x" * 900)

        result = self._download(self._serve(handle))

        self.assertEqual(result.kind, vp.RUN_PASSED)
        self.assertGreaterEqual(result.received, vp.TARGET_BYTES)
        # Объём набран запросами по одному соединению, а не одним ответом.
        self.assertGreater(result.requests, 30)
        self.assertTrue(seen[0].startswith(b"GET /page HTTP/1.1\r\n"))
        self.assertIn(b"Connection: keep-alive", seen[0])

    def test_chunked_answers_are_read_to_the_end(self) -> None:
        def handle(tls) -> None:
            for _head in self._requests(tls):
                tls.sendall(
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                    b"7d0\r\n" + b"a" * 2000 + b"\r\n" + b"7d0\r\n" + b"b" * 2000 + b"\r\n0\r\n\r\n"
                )

        result = self._download(self._serve(handle))

        self.assertEqual(result.kind, vp.RUN_PASSED)
        # Ответ с заголовками — чуть больше 4 КБ, до 48 КБ нужно ровно тринадцать: каждый
        # ответ дочитан до конца, и следующий запрос не съехал.
        self.assertEqual(result.requests, 13)

    def test_connection_that_freezes_is_stalled_with_its_volume(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def handle(tls) -> None:
            sent = 0
            for _head in self._requests(tls):
                if sent >= 14_000:
                    release.wait(3)
                    return
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1960\r\n\r\n" + b"x" * 1960)
                sent += 2000

        result = self._download(self._serve(handle), stall=0.4)

        self.assertEqual(result.kind, vp.RUN_STALLED)
        self.assertTrue(vp.CUT_MIN_BYTES <= result.received <= vp.CUT_MAX_BYTES, result.received)
        self.assertEqual(vp.judge(vp.VolumeFacts(result, result)).code, vp.VOLUME_CUT)

    def test_server_closing_after_answer_is_not_a_stall(self) -> None:
        def handle(tls) -> None:
            next(self._requests(tls))
            tls.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 500\r\n\r\n" + b"x" * 500)

        result = self._download(self._serve(handle))

        self.assertEqual((result.kind, result.requests), (vp.RUN_CLOSED, 1))
        self.assertEqual(vp.judge(vp.VolumeFacts(result)).code, vp.VOLUME_UNKNOWN)

    def test_tiny_answers_stop_the_probe_early(self) -> None:
        """Сервер отвечает по 150 байт: объём не набрать, десятки запросов слать незачем."""
        def handle(tls) -> None:
            for _head in self._requests(tls):
                tls.sendall(b"HTTP/1.1 204 No Content\r\nX-Pad: " + b"p" * 110 + b"\r\n\r\n")

        result = self._download(self._serve(handle))

        self.assertEqual((result.kind, result.requests), (vp.RUN_CLOSED, vp.ESTIMATE_AFTER))
        self.assertEqual(vp.judge(vp.VolumeFacts(result)).code, vp.VOLUME_UNKNOWN)

    def test_head_requests_carry_no_body(self) -> None:
        methods: list[bytes] = []

        def handle(tls) -> None:
            for head in self._requests(tls):
                methods.append(head.split(b" ", 1)[0])
                # Длина тела названа, но самого тела в ответе на HEAD нет.
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 50000\r\nX-Pad: " + b"p" * 800 + b"\r\n\r\n")

        result = self._download(self._serve(handle), method="HEAD", target=8_000)

        self.assertEqual(result.kind, vp.RUN_PASSED)
        self.assertEqual(set(methods), {b"HEAD"})
        self.assertGreater(result.requests, 5)

    def test_big_body_is_not_read_past_the_target(self) -> None:
        def handle(tls) -> None:
            next(self._requests(tls))
            try:
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5000000\r\n\r\n" + b"x" * 5_000_000)
            except (OSError, ssl.SSLError):
                pass

        result = self._download(self._serve(handle))

        self.assertEqual((result.kind, result.requests), (vp.RUN_PASSED, 1))
        self.assertLess(result.received, 1_000_000)

    def test_cancelled_probe_reports_cancel(self) -> None:
        token = SocketCancel()
        token.cancel()

        self.assertEqual(vp.download(HOST, "127.0.0.1", "/", port=9, cancel=token).kind, vp.RUN_CANCELLED)


if __name__ == "__main__":
    unittest.main()
