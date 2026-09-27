from __future__ import annotations

import asyncio
import hashlib
import os
import struct
import time
import unittest
from unittest.mock import patch

from telegram_proxy.proxy import session as session_module
from telegram_proxy.proxy import ws as ws_module
from telegram_proxy.proxy.aes_ctr import AesCtrStream, aes_ctr_keystream
from telegram_proxy.proxy.obfs import ABRIDGED, PADDED, PacketReader, encode_packet
from telegram_proxy.proxy.routing import UpstreamProxyConfig
from telegram_proxy.wss_proxy import TelegramWSProxy


SECRET_HEX = "00112233445566778899aabbccddeeff"
RELAY_IP = "149.154.167.220"


def make_client_header(tag: bytes, dc: int, secret: bytes = b""):
    while True:
        header = bytearray(os.urandom(64))
        if header[0] != 0xEF and bytes(header[4:8]) != b"\0\0\0\0" and bytes(header[:4]) not in (b"\xdd" * 4, b"\xee" * 4):
            break
    prekey_iv = bytes(header[8:56])
    key, iv = prekey_iv[:32], prekey_iv[32:48]
    reverse = prekey_iv[::-1]
    back_key, back_iv = reverse[:32], reverse[32:48]
    if secret:
        key = hashlib.sha256(key + secret).digest()
        back_key = hashlib.sha256(back_key + secret).digest()
    tail = tag + struct.pack("<h", dc) + b"\0\0"
    keystream = aes_ctr_keystream(key, iv, 64)
    header[56:64] = bytes(a ^ b for a, b in zip(tail, keystream[56:64]))
    to_server = AesCtrStream(key, iv)
    to_server.update(b"\0" * 64)
    return bytes(header), to_server, AesCtrStream(back_key, back_iv)


def answer_for(packet: bytes) -> bytes:
    """Ответ «Telegram» того же вида: заголовок пакета тот же, тело перевёрнуто."""
    return packet[:20] + packet[20:][::-1]


def rpc_packet(body_len: int) -> bytes:
    """Незашифрованный MTProto-пакет: auth_key_id=0, msg_id, длина, тело."""
    return b"\0" * 8 + os.urandom(8) + struct.pack("<I", body_len) + os.urandom(body_len)


class FakeTelegramWs:
    """WebSocket, за которым «Telegram»: принимает заголовок и отвечает эхом пакетов."""

    def __init__(self, target: ws_module.WsTarget, *, answer: bool, answer_delay: float = 0.0):
        self.target = target
        self.answer = answer
        self.answer_delay = answer_delay
        # Сырой поток сокета: сессия смотрит в него, когда клиент ушёл раньше ответа.
        self.reader = asyncio.StreamReader()
        self.frames: list[bytes] = []
        self.packets: list[bytes] = []
        self.header_tail = b""
        self.opened_at = time.monotonic()
        self.is_closing = False
        self._inbox: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._decrypt: AesCtrStream | None = None
        self._encrypt: AesCtrStream | None = None
        self._reader = PacketReader(ABRIDGED)

    def has_unread_data(self) -> bool:
        return False

    async def send_many(self, chunks: list[bytes]) -> None:
        for chunk in chunks:
            self.frames.append(chunk)
            if self._decrypt is None:
                header = chunk[:64]
                self._decrypt = AesCtrStream(header[8:40], header[40:56])
                self.header_tail = self._decrypt.update(header)[56:64]
                reverse = header[8:56][::-1]
                self._encrypt = AesCtrStream(reverse[:32], reverse[32:48])
                chunk = chunk[64:]
            packets = self._reader.feed(self._decrypt.update(chunk))
            self.packets.extend(packets)
            if self.answer:
                for packet in packets:
                    reply = self._encrypt.update(encode_packet(ABRIDGED, answer_for(packet)))
                    asyncio.get_running_loop().call_later(self.answer_delay, self._deliver, reply)

    def _deliver(self, reply: bytes) -> None:
        if self.is_closing:
            return
        self.reader.feed_data(reply)
        self._inbox.put_nowait(reply)

    async def recv(self) -> bytes | None:
        return await self._inbox.get()

    async def close(self) -> None:
        self.is_closing = True
        self.reader.feed_eof()
        self._inbox.put_nowait(None)


async def serve_fake_socks_telegram(requests: list[tuple[str, int]]):
    """Внешний SOCKS5, за которым «Telegram» по обычному TCP."""

    async def handler(reader, writer):
        try:
            greeting = await reader.readexactly(2)
            await reader.readexactly(greeting[1])
            writer.write(b"\x05\x00")
            head = await reader.readexactly(4)
            host = ".".join(str(b) for b in await reader.readexactly(4)) if head[3] == 1 else ""
            port = struct.unpack("!H", await reader.readexactly(2))[0]
            requests.append((host, port))
            writer.write(b"\x05\x00\x00\x01" + b"\0" * 6)
            header = await reader.readexactly(64)
            decrypt = AesCtrStream(header[8:40], header[40:56])
            decrypt.update(header)
            reverse = header[8:56][::-1]
            encrypt = AesCtrStream(reverse[:32], reverse[32:48])
            packets = PacketReader(ABRIDGED)
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for packet in packets.feed(decrypt.update(data)):
                    writer.write(encrypt.update(encode_packet(ABRIDGED, answer_for(packet))))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handler, "127.0.0.1", 0)


class SessionScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sockets: list[FakeTelegramWs] = []
        self.logs: list[str] = []
        self.refuse_ws = False
        self.relay_answer_delay: float | None = None

        async def fake_connect(target, **_kwargs):
            if self.refuse_ws:
                raise ws_module.WsConnectError(ws_module.STAGE_TCP, "refused")
            if target.connect_host == RELAY_IP:
                delay = self.relay_answer_delay
                sock = FakeTelegramWs(target, answer=delay is not None, answer_delay=delay or 0.0)
            else:
                sock = FakeTelegramWs(target, answer=True)
            self.sockets.append(sock)
            return sock

        patches = [
            patch.object(ws_module, "connect", fake_connect),
            patch.object(session_module, "WATCHDOG_SECONDS", 0.3),
            patch.object(session_module, "MEDIA_WATCHDOG_SECONDS", 0.3),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def _start(self, **kwargs) -> TelegramWSProxy:
        proxy = TelegramWSProxy(port=0, pool_size=0, on_log=self.logs.append, **kwargs)
        await proxy.start()
        self.addAsyncCleanup(proxy.stop)
        self.port = proxy._servers[0].sockets[0].getsockname()[1]
        return proxy

    async def _socks5_connect(self, host: str, port: int):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"\x05\x01\x00")
        self.assertEqual(await reader.readexactly(2), b"\x05\x00")
        writer.write(b"\x05\x01\x00\x01" + bytes(int(x) for x in host.split(".")) + struct.pack("!H", port))
        reply = await reader.readexactly(10)
        self.assertEqual(reply[1], 0)
        return reader, writer

    async def _read_packets(self, reader, cipher, framing: str, count: int) -> list[bytes]:
        packet_reader = PacketReader(framing)
        packets: list[bytes] = []
        while len(packets) < count:
            data = await asyncio.wait_for(reader.read(65536), timeout=3)
            self.assertTrue(data, "proxy closed before the answer")
            packets.extend(packet_reader.feed(cipher.update(data)))
        return packets

    async def test_silent_relay_is_replaced_by_front_without_client_noticing(self) -> None:
        proxy = await self._start(mode="socks5")
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        first, second = rpc_packet(40), rpc_packet(1200)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, first) + encode_packet(ABRIDGED, second)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, ABRIDGED, 2)
        self.assertEqual(answers, [answer_for(first), answer_for(second)])
        writer.close()

        relay, front = self.sockets[0], self.sockets[1]
        self.assertEqual((relay.target.connect_host, relay.target.sni), (RELAY_IP, "kws2.web.telegram.org"))
        self.assertTrue(front.target.sni.startswith("kws2.") and front.target.sni.endswith(".co.uk"))
        # Один пакет — один кадр; заголовок едет в кадре с первым пакетом.
        for sock in (relay, front):
            self.assertEqual(len(sock.frames), 2)
            self.assertGreater(len(sock.frames[0]), 64)
            self.assertEqual(sock.packets, [first, second])
        # Фронту — тег abridged и +dc, без секрета.
        self.assertEqual(front.header_tail[:4], b"\xef" * 4)
        self.assertEqual(struct.unpack("<h", front.header_tail[4:6])[0], 2)
        self.assertEqual(proxy.stats.cloudflare_connections, 1)
        self.assertTrue(any("route=WSS" in line and "result=error" in line for line in self.logs))
        self.assertTrue(any("route=Cloudflare" in line and "result=connected" in line for line in self.logs))

    async def test_mtproxy_padded_client_is_rewrapped_to_abridged(self) -> None:
        await self._start(mode="mtproxy", mtproxy_secret=SECRET_HEX)
        self.sockets.clear()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        header, to_server, from_server = make_client_header(b"\xdd" * 4, -4, bytes.fromhex(SECRET_HEX))
        packet = rpc_packet(28)
        writer.write(header + to_server.update(encode_packet(PADDED, packet)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, PADDED, 1)
        self.assertEqual(answers, [answer_for(packet)])
        writer.close()

        front = next(sock for sock in self.sockets if sock.answer)
        self.assertEqual(front.packets, [packet])
        self.assertEqual(struct.unpack("<h", front.header_tail[4:6])[0], 4)
        relay = self.sockets[0]
        self.assertEqual(relay.target.sni, "kws4-1.web.telegram.org")

    async def _leave_early(self) -> TelegramWSProxy:
        proxy = await self._start(mode="socks5")
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, _from_server = make_client_header(b"\xef" * 4, 2)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, rpc_packet(16))))
        await writer.drain()
        # Desktop на первых попытках ждёт ответа всего ~1 с — уходит раньше сторожа.
        await asyncio.sleep(0.1)
        writer.close()
        await asyncio.sleep(0.5)
        return proxy

    async def test_silent_relay_is_blamed_even_if_client_left_early(self) -> None:
        proxy = await self._leave_early()
        self.assertTrue(
            any("route=WSS" in line and "result=error" in line and "client left early" in line for line in self.logs),
            self.logs,
        )
        self.assertEqual(proxy.stats.recv_zero_count, 1)

    async def test_relay_answering_after_client_left_is_not_blamed(self) -> None:
        self.relay_answer_delay = 0.2
        proxy = await self._leave_early()
        self.assertTrue(any("answered later" in line for line in self.logs), self.logs)
        self.assertFalse(any("result=error" in line for line in self.logs), self.logs)
        self.assertEqual(proxy.stats.recv_zero_count, 0)

    async def test_silent_pooled_socket_is_not_retried_fresh(self) -> None:
        proxy = await self._start(mode="socks5")
        pooled = FakeTelegramWs(ws_module.WsTarget(RELAY_IP, "kws2.web.telegram.org"), answer=False)
        proxy.ws_pool.take = lambda route: pooled if route.kind == "relay" else None
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        packet = rpc_packet(16)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, packet)))
        await writer.drain()

        self.assertEqual(await self._read_packets(reader, from_server, ABRIDGED, 1), [answer_for(packet)])
        writer.close()
        # Молчащий сокет из пула — это отказ релея: следующим идёт фронт,
        # а не второе 5,5-секундное ожидание свежего релея.
        self.assertFalse(any(sock.target.connect_host == RELAY_IP for sock in self.sockets))
        self.assertFalse(any("retry fresh" in line for line in self.logs))

    async def test_country_socks_carries_traffic_when_wss_is_closed(self) -> None:
        requests: list[tuple[str, int]] = []
        socks = await serve_fake_socks_telegram(requests)
        self.addAsyncCleanup(self._close_server, socks)
        socks_port = socks.sockets[0].getsockname()[1]
        upstream = UpstreamProxyConfig(
            enabled=True, host="127.0.0.1", port=socks_port, mode="fallback", preset_id="ee", preset_name="Estonia"
        )
        self.refuse_ws = True
        proxy = await self._start(mode="socks5", upstream_config=upstream)
        reader, writer = await self._socks5_connect("149.154.175.50", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 1)
        packets = [rpc_packet(20), rpc_packet(300)]
        writer.write(header + to_server.update(b"".join(encode_packet(ABRIDGED, item) for item in packets)))
        await writer.drain()

        answers = await self._read_packets(reader, from_server, ABRIDGED, 2)
        self.assertEqual(answers, [answer_for(item) for item in packets])
        self.assertEqual(requests, [("149.154.175.50", 443)])
        self.assertEqual(proxy.stats.upstream_connections, 1)
        writer.close()

    async def test_stop_does_not_wait_for_live_telegram_connection(self) -> None:
        proxy = TelegramWSProxy(port=0, pool_size=0, mode="socks5", on_log=self.logs.append)
        await proxy.start()
        self.port = proxy._servers[0].sockets[0].getsockname()[1]
        reader, writer = await self._socks5_connect("149.154.167.51", 443)
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        writer.write(header + to_server.update(encode_packet(ABRIDGED, rpc_packet(16))))
        await writer.drain()
        await self._read_packets(reader, from_server, ABRIDGED, 1)

        await asyncio.wait_for(proxy.stop(), timeout=3)
        self.assertFalse(proxy.is_running)
        writer.close()

    @staticmethod
    async def _close_server(server) -> None:
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
