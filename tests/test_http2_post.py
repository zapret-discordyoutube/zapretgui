from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

from utils import http2_post

PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


def _frame(kind: int, flags: int, stream: int, payload: bytes = b"") -> bytes:
    return struct.pack("!I", len(payload))[1:] + bytes([kind, flags]) + struct.pack("!I", stream) + payload


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _read_frames_until_end_of_request(sock: socket.socket) -> list[tuple[int, int, int, bytes]]:
    frames = []
    while True:
        header = _read_exact(sock, 9)
        size = int.from_bytes(header[:3], "big")
        kind, flags, stream = header[3], header[4], struct.unpack("!I", header[5:])[0]
        frames.append((kind, flags, stream, _read_exact(sock, size)))
        if kind == 0 and flags & 0x1:
            return frames


class StatusTests(unittest.TestCase):
    def test_ready_made_status_lines(self) -> None:
        self.assertEqual(http2_post.read_status(b"\x88"), 200)
        self.assertEqual(http2_post.read_status(b"\x8d"), 404)

    def test_status_written_as_plain_text(self) -> None:
        self.assertEqual(http2_post.read_status(b"\x48\x03505"), 505)
        self.assertEqual(http2_post.read_status(b"\x08\x03403"), 403)

    def test_status_written_compressed(self) -> None:
        # «505»: 5 → 011011, 0 → 00000, 5 → 011011, остаток добит единицами.
        self.assertEqual(http2_post.read_status(b"\x48\x83\x6c\x0d\xff"), 505)
        # «429»: 4 → 011010, 2 → 00010, 9 → 011111.
        self.assertEqual(http2_post.read_status(b"\x48\x83\x68\x4f\xff"), 429)

    def test_dictionary_size_notice_before_status_is_skipped(self) -> None:
        self.assertEqual(http2_post.read_status(b"\x20\x88"), 200)
        self.assertEqual(http2_post.read_status(b"\x3f\xe1\x1f\x88"), 200)

    def test_unreadable_block_is_none_not_error(self) -> None:
        for block in (b"", b"\x40\x03abc\x03def", b"\x48", b"\x48\x03ab"):
            with self.subTest(block=block):
                self.assertIsNone(http2_post.read_status(block))


class ExchangeTests(unittest.TestCase):
    def _exchange(self, serve, *, timeout: float = 3.0):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        seen: dict = {}

        def _run() -> None:
            try:
                seen["preface"] = _read_exact(server, len(PREFACE))
                seen["frames"] = _read_frames_until_end_of_request(server)
                serve(server)
            except OSError:
                pass

        threading.Thread(target=_run, daemon=True).start()
        until = time.monotonic() + timeout

        def remaining() -> float:
            left = until - time.monotonic()
            if left <= 0:
                raise socket.timeout()
            return left

        result = http2_post.post(
            client,
            authority="dns.test",
            path="/dns-query",
            body=b"QUERY",
            content_type="application/dns-message",
            remaining=remaining,
        )
        return result, seen

    def test_request_is_sent_and_answer_is_read(self) -> None:
        def serve(sock) -> None:
            sock.sendall(
                _frame(4, 0, 0)  # настройки сервера
                + _frame(6, 0, 0, b"12345678")  # проверка связи
                + _frame(1, 0x4, 1, b"\x88")  # заголовки: 200
                + _frame(0, 0, 1, b"ANS")
                + _frame(0, 0x1, 1, b"WER")
            )
            self.replies = _read_exact(sock, 9 + 9 + 8)

        (status, body), seen = self._exchange(serve)

        self.assertEqual((status, body), (200, b"ANSWER"))
        self.assertEqual(seen["preface"], PREFACE)
        kinds = [(kind, stream) for kind, _flags, stream, _payload in seen["frames"]]
        self.assertEqual(kinds, [(4, 0), (1, 1), (0, 1)])
        headers = seen["frames"][1][3]
        for part in (b"\x83", b"\x87", b"/dns-query", b"dns.test", b"application/dns-message", b"5"):
            self.assertIn(part, headers)
        self.assertEqual(seen["frames"][2][3], b"QUERY")
        # Клиент подтвердил настройки сервера и ответил на проверку связи.
        self.assertEqual(self.replies, _frame(4, 0x1, 0) + _frame(6, 0x1, 0, b"12345678"))

    def test_padded_data_and_error_status(self) -> None:
        def serve(sock) -> None:
            sock.sendall(_frame(1, 0x4, 1, b"\x8d") + _frame(0, 0x1 | 0x8, 1, b"\x03no" + b"\x00" * 3))

        (status, body), _seen = self._exchange(serve)

        self.assertEqual((status, body), (404, b"no"))

    def test_headers_only_answer_ends_exchange(self) -> None:
        (status, body), _seen = self._exchange(lambda sock: sock.sendall(_frame(1, 0x4 | 0x1, 1, b"\x8e")))

        self.assertEqual((status, body), (500, b""))

    def test_refused_stream_and_goaway_are_errors(self) -> None:
        for reply in (_frame(3, 0, 1, b"\x00\x00\x00\x07"), _frame(7, 0, 0, b"\x00" * 8)):
            with self.subTest(reply=reply[:4]), self.assertRaises(http2_post.Http2Error):
                self._exchange(lambda sock, reply=reply: sock.sendall(reply))

    def test_server_that_hangs_up_is_error_and_silent_server_is_timeout(self) -> None:
        with self.assertRaises(http2_post.Http2Error):
            self._exchange(lambda sock: sock.close())
        with self.assertRaises(socket.timeout):
            self._exchange(lambda _sock: time.sleep(1), timeout=0.3)

    def test_frames_of_other_streams_are_ignored(self) -> None:
        def serve(sock) -> None:
            sock.sendall(_frame(0, 0x1, 3, b"other") + _frame(1, 0x4, 1, b"\x88") + _frame(0, 0x1, 1, b"ok"))

        (status, body), _seen = self._exchange(serve)

        self.assertEqual((status, body), (200, b"ok"))


if __name__ == "__main__":
    unittest.main()
