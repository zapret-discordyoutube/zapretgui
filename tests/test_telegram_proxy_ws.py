from __future__ import annotations

import asyncio
import base64
import hashlib
import shutil
import ssl
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from telegram_proxy.proxy import ws


HOST_NAME = "kws2.test.local"


def _make_cert(directory: Path) -> tuple[Path, Path]:
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", f"/CN={HOST_NAME}", "-addext", f"subjectAltName=DNS:{HOST_NAME}",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def _accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + ws.WS_GUID).digest()).decode()


def _server_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    head = bytes(((0x80 if fin else 0) | opcode,))
    size = len(payload)
    if size < 126:
        head += bytes((size,))
    else:
        head += bytes((126,)) + struct.pack(">H", size)
    return head + payload


async def _read_client_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    assert head[1] & 0x80, "client frames must be masked"
    size = head[1] & 0x7F
    if size == 126:
        size = struct.unpack(">H", await reader.readexactly(2))[0]
    elif size == 127:
        size = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4)
    data = await reader.readexactly(size)
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


@unittest.skipUnless(shutil.which("openssl"), "нужен openssl для тестового сертификата")
class WebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cert, cls.key = _make_cert(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    async def _serve(self, handler):
        server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        server_ctx.load_cert_chain(self.cert, self.key)
        server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_ctx)
        self.addAsyncCleanup(self._close_server, server)
        port = server.sockets[0].getsockname()[1]
        client_ctx = ssl.create_default_context(cafile=str(self.cert))
        target = ws.WsTarget(connect_host="127.0.0.1", sni=HOST_NAME, port=port)
        return target, client_ctx

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()

    @staticmethod
    async def _read_request(reader) -> dict[str, str]:
        raw = (await reader.readuntil(b"\r\n\r\n")).decode()
        lines = raw.split("\r\n")
        headers = {"_line": lines[0]}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        return headers

    async def test_busy_front_is_retried_on_same_tls_connection(self) -> None:
        requests: list[dict[str, str]] = []

        async def handler(reader, writer):
            for _ in range(2):
                requests.append(await self._read_request(reader))
                writer.write(b"HTTP/1.1 503 Service Unavailable\r\ncontent-length: 0\r\n\r\n")
                await writer.drain()
            headers = await self._read_request(reader)
            requests.append(headers)
            writer.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {_accept(headers['sec-websocket-key'])}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            opcode, data = await _read_client_frame(reader)
            writer.write(_server_frame(0x9, b"hb"))
            writer.write(_server_frame(0x2, data[:3], fin=False))
            writer.write(_server_frame(0x0, data[3:]))
            await writer.drain()
            await _read_client_frame(reader)  # pong
            writer.close()

        target, ctx = await self._serve(handler)
        sock = await ws.connect(target, ssl_context=ctx)
        await sock.send(b"hello world")
        self.assertEqual(await sock.recv(), b"hello world")
        self.assertIsNone(await sock.recv())
        await sock.close()

        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[-1]["_line"], "GET /apiws HTTP/1.1")
        self.assertEqual(requests[-1]["host"], f"{HOST_NAME}:{target.port}")
        self.assertEqual(requests[-1]["sec-websocket-protocol"], "binary")

    async def test_wrong_accept_and_http_error_are_upgrade_stage_errors(self) -> None:
        async def handler(reader, writer):
            await self._read_request(reader)
            writer.write(b"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: bad\r\n\r\n")
            await writer.drain()
            writer.close()

        target, ctx = await self._serve(handler)
        with self.assertRaises(ws.WsConnectError) as caught:
            await ws.connect(target, ssl_context=ctx)
        self.assertEqual(caught.exception.stage, ws.STAGE_UPGRADE)
        self.assertTrue(caught.exception.tcp_reached)

    async def test_certificate_for_other_name_is_rejected(self) -> None:
        async def handler(reader, writer):
            writer.close()

        target, ctx = await self._serve(handler)
        other = ws.WsTarget(connect_host=target.connect_host, sni="other.test.local", port=target.port)
        with self.assertRaises(ws.WsConnectError) as caught:
            await ws.connect(other, ssl_context=ctx)
        self.assertEqual(caught.exception.stage, ws.STAGE_TLS)

    async def test_closed_port_is_tcp_stage_error(self) -> None:
        probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        with self.assertRaises(ws.WsConnectError) as caught:
            await ws.connect(ws.WsTarget("127.0.0.1", HOST_NAME, port=port))
        self.assertEqual(caught.exception.stage, ws.STAGE_TCP)
        self.assertFalse(caught.exception.tcp_reached)


if __name__ == "__main__":
    unittest.main()
