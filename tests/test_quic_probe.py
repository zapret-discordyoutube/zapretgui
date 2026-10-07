from __future__ import annotations

import socket
import struct
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from diagnostics import quic_probe as qp
from utils import quic_initial as q
from utils.socket_cancel import SocketCancel

H = bytes.fromhex


class CipherTests(unittest.TestCase):
    """Своё шифрование сверяется с образцами из стандартов."""

    def test_aes_block_matches_fips_197(self) -> None:
        key = H("000102030405060708090a0b0c0d0e0f")
        block = H("00112233445566778899aabbccddeeff")

        self.assertEqual(q.aes128_encrypt_block(key, block).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_gcm_matches_nist_cases(self) -> None:
        self.assertEqual(q.aes128_gcm_seal(bytes(16), bytes(12), b"", b"").hex(), "58e2fccefa7e3061367f1d57a4e7455a")
        self.assertEqual(
            q.aes128_gcm_seal(bytes(16), bytes(12), bytes(16), b"").hex(),
            "0388dace60b6a392f328c2b971b2fe78ab6e47d42cec13bdf53a67b21257bddf",
        )

    def test_gcm_with_extra_data_and_partial_block(self) -> None:
        """Образец 4 из описания GCM: есть дополнительные данные, последний блок неполный."""
        sealed = q.aes128_gcm_seal(
            H("feffe9928665731c6d6a8f9467308308"),
            H("cafebabefacedbaddecaf888"),
            H(
                "d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
                "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39"
            ),
            H("feedfacedeadbeeffeedfacedeadbeefabaddad2"),
        )

        self.assertEqual(
            sealed.hex(),
            "42831ec2217774244b7221b784d0d49ce3aa212f2c02a4e035c17e2329aca12e"
            "21d514b25466931c7d8f6a5aac84aa051ba30b396a0aac973d58e091"
            "5bc94fbc3221a5db94fae95ae7121a47",
        )

    def test_wrong_sizes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            q.aes128_encrypt_block(bytes(15), bytes(16))
        with self.assertRaises(ValueError):
            q.aes128_encrypt_block(bytes(16), bytes(8))
        with self.assertRaises(ValueError):
            q.aes128_gcm_seal(bytes(16), bytes(8), b"", b"")

    def test_initial_keys_match_rfc_9001(self) -> None:
        key, iv, header_key = q.initial_keys(H("8394c8f03e515708"))

        self.assertEqual(key.hex(), "1f369613dd76d5467730efcbe3b1a22d")
        self.assertEqual(iv.hex(), "fa044b2f42a3fd3b46fb255c")
        self.assertEqual(header_key.hex(), "9f50449e04a0e810283a1e9933adedd2")


class PacketTests(unittest.TestCase):
    def _build(self, name="example.com"):
        return q.build_initial(name, destination_id=H("8394c8f03e515708"), source_id=H("1122334455667788"))

    def test_packet_has_long_header_version_and_both_connection_ids(self) -> None:
        packet = self._build()
        data = packet.datagram

        self.assertEqual(len(data), 1200)
        self.assertEqual(data[0] & 0xF0, 0xC0)
        self.assertEqual(struct.unpack("!I", data[1:5])[0], 1)
        self.assertEqual((data[5], data[6:14]), (8, packet.destination_id))
        self.assertEqual((data[14], data[15:23]), (8, packet.source_id))
        self.assertEqual(data[23], 0)  # жетона нет
        # Длина остатка пакета записана двумя байтами и сходится с настоящей.
        self.assertEqual(struct.unpack("!H", data[24:26])[0] & 0x3FFF, len(data) - 26)

    def test_site_name_is_hidden_by_encryption(self) -> None:
        self.assertNotIn(b"example.com", self._build().datagram)

    def test_every_packet_is_a_new_connection(self) -> None:
        first, second = q.build_initial("example.com"), q.build_initial("example.com")

        self.assertNotEqual(first.destination_id, second.destination_id)
        self.assertNotEqual(first.source_id, second.source_id)

    def test_packet_without_name_and_with_cyrillic_name_are_built(self) -> None:
        self.assertEqual(len(q.build_initial(None).datagram), 1200)
        self.assertEqual(len(q.build_initial("пример.рф").datagram), 1200)

    def test_reply_is_recognised_by_our_connection_id(self) -> None:
        packet = self._build()
        reply = bytes([0xC0]) + struct.pack("!I", 1) + bytes([8]) + packet.source_id + bytes([8]) + bytes(8) + bytes(20)
        # Сервер может предложить другую версию или попросить повторить — это тоже ответ.
        negotiation = bytes([0x80]) + bytes(4) + bytes([8]) + packet.source_id + bytes(10)
        foreign = bytes([0xC0]) + struct.pack("!I", 1) + bytes([8]) + bytes(8) + bytes(30)

        self.assertTrue(q.is_reply_to(reply, packet))
        self.assertTrue(q.is_reply_to(negotiation, packet))
        self.assertFalse(q.is_reply_to(foreign, packet))
        # Короткий заголовок и обрывок — не ответ на первый пакет.
        self.assertFalse(q.is_reply_to(bytes([0x40]) + packet.source_id + bytes(20), packet))
        self.assertFalse(q.is_reply_to(b"\xc0\x00", packet))


try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # в поставку программы эта библиотека не входит
    AESGCM = None


@unittest.skipIf(AESGCM is None, "для независимой расшифровки нужна сторонняя библиотека")
class IndependentDecryptionTests(unittest.TestCase):
    """Пакет расшифровывается посторонней библиотекой так, как это сделает сервер."""

    def _open(self, packet: q.InitialPacket) -> bytes:
        data = packet.datagram
        key, iv, header_key = q.initial_keys(packet.destination_id)
        number_offset = 26
        # Снимаем защиту заголовка: образец — 16 байт через 4 байта от начала номера пакета.
        sample = data[number_offset + 4 : number_offset + 20]
        encryptor = Cipher(algorithms.AES(header_key), modes.ECB()).encryptor()
        mask = encryptor.update(sample) + encryptor.finalize()
        first = data[0] ^ (mask[0] & 0x0F)
        number_size = (first & 0x03) + 1
        number = bytes(a ^ b for a, b in zip(data[number_offset : number_offset + number_size], mask[1:]))
        header = bytes([first]) + data[1:number_offset] + number
        nonce = bytes(a ^ b for a, b in zip(iv, number.rjust(12, b"\x00")))
        return AESGCM(key).decrypt(nonce, data[number_offset + number_size :], header)

    def test_server_would_read_client_hello_with_site_name(self) -> None:
        packet = q.build_initial("www.youtube.com")
        payload = self._open(packet)

        self.assertEqual(payload[0], 0x06)  # кадр с данными рукопожатия
        self.assertEqual(payload[1], 0x00)  # с самого начала
        size = struct.unpack("!H", payload[2:4])[0] & 0x3FFF
        hello = payload[4 : 4 + size]
        self.assertEqual(hello[0], 0x01)  # приветствие клиента
        self.assertEqual(int.from_bytes(hello[1:4], "big"), len(hello) - 4)
        self.assertIn(b"\x00\x0fwww.youtube.com", hello)
        self.assertIn(b"\x02h3", hello)
        # Наш номер соединения повторён в параметрах: без этого сервер отвергнет пакет.
        self.assertIn(b"\x0f\x08" + packet.source_id, hello)
        # После приветствия — только заполнение до 1200 байт.
        self.assertEqual(set(payload[4 + size :]), {0})

    def test_packet_without_name_has_no_name_extension(self) -> None:
        payload = self._open(q.build_initial(None))

        self.assertNotIn(b"example.com", payload)
        self.assertIn(b"\x02h3", payload)


class _UdpServer:
    def __init__(self, reply) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(3)
        self.port = self.sock.getsockname()[1]
        self.received: list[bytes] = []
        self._reply = reply
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            while True:
                data, address = self.sock.recvfrom(4096)
                self.received.append(data)
                answer = self._reply(data, len(self.received))
                if answer is not None:
                    self.sock.sendto(answer, address)
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


def _answer_to(data: bytes) -> bytes:
    """Ответ сервера: наш номер отправителя становится номером получателя."""
    source_id = data[15:23]
    return bytes([0xC0]) + struct.pack("!I", 1) + bytes([8]) + source_id + bytes([8]) + bytes(8) + bytes(40)


class HelloTests(unittest.TestCase):
    def _server(self, reply) -> _UdpServer:
        server = _UdpServer(reply)
        self.addCleanup(server.close)
        return server

    def test_answer_gives_time_and_one_packet_is_enough(self) -> None:
        server = self._server(lambda data, _count: _answer_to(data))

        elapsed = qp.quic_hello("127.0.0.1", "example.com", port=server.port, timeout=2)

        self.assertIsNotNone(elapsed)
        self.assertEqual(len(server.received), 1)
        self.assertEqual(len(server.received[0]), 1200)

    def test_silence_is_retried_with_a_new_connection(self) -> None:
        server = self._server(lambda _data, _count: None)

        elapsed = qp.quic_hello("127.0.0.1", "example.com", port=server.port, attempts=2, timeout=0.2)

        self.assertIsNone(elapsed)
        self.assertEqual(len(server.received), 2)
        self.assertNotEqual(server.received[0][6:14], server.received[1][6:14])

    def test_one_lost_packet_is_never_enough_by_default(self) -> None:
        self.assertGreaterEqual(qp.ATTEMPTS, 2)

    def test_lost_first_packet_does_not_mean_blocked(self) -> None:
        server = self._server(lambda data, count: _answer_to(data) if count == 2 else None)

        self.assertIsNotNone(qp.quic_hello("127.0.0.1", "example.com", port=server.port, attempts=2, timeout=0.3))

    def test_packet_for_another_connection_is_not_an_answer(self) -> None:
        foreign = bytes([0xC0]) + struct.pack("!I", 1) + bytes([8]) + bytes(8) + bytes(40)
        server = self._server(lambda _data, _count: foreign)

        self.assertIsNone(qp.quic_hello("127.0.0.1", "example.com", port=server.port, attempts=1, timeout=0.3))

    def test_cancelled_probe_sends_nothing(self) -> None:
        server = self._server(lambda data, _count: _answer_to(data))
        cancel = SocketCancel()
        cancel.cancel()

        self.assertIsNone(qp.quic_hello("127.0.0.1", "example.com", port=server.port, cancel=cancel))
        self.assertEqual(server.received, [])


class JudgeTests(unittest.TestCase):
    def test_answer_with_site_name_is_working_quic(self) -> None:
        verdict = qp.judge(qp.QuicFacts("youtube.com", real_ms=13.4, neutral_ms=None, nameless_ms=None))

        self.assertEqual((verdict.code, verdict.text), (qp.QUIC_OK, "отвечает за 13 мс"))

    def test_silence_with_site_name_only_is_block_by_name(self) -> None:
        for facts, how in (
            (qp.QuicFacts("rutracker.org", neutral_ms=43.0, nameless_ms=None), "с посторонним именем"),
            (qp.QuicFacts("rutracker.org", neutral_ms=None, nameless_ms=44.0), "без имени"),
        ):
            with self.subTest(how=how):
                verdict = qp.judge(facts)
                self.assertEqual(verdict.code, qp.QUIC_BLOCKED_BY_NAME)
                self.assertIn("rutracker.org", verdict.text)
                self.assertIn(how, verdict.text)

    def test_silence_everywhere_is_not_a_block(self) -> None:
        """Так выглядит сервер без QUIC (например, GitHub): блокировкой это не называется."""
        verdict = qp.judge(qp.QuicFacts("github.com"))

        self.assertEqual(verdict.code, qp.QUIC_SILENT)
        self.assertIn("не поддерживает QUIC", verdict.text)

    def test_cancelled_probe_gives_no_verdict(self) -> None:
        self.assertIsNone(qp.judge(qp.QuicFacts("youtube.com", cancelled=True)))
        self.assertIsNone(qp.judge(qp.QuicFacts("youtube.com", neutral_ms=5.0, cancelled=True)))
        # Ответ успел прийти до отмены — он настоящий.
        self.assertEqual(qp.judge(qp.QuicFacts("youtube.com", real_ms=5.0, cancelled=True)).code, qp.QUIC_OK)


class CollectTests(unittest.TestCase):
    def test_three_packets_go_to_the_same_address(self) -> None:
        asked: list[tuple] = []

        def hello(ip, name, **_kwargs):
            asked.append((ip, name))
            return {"youtube.com": None, qp.NEUTRAL_NAME: 40.0, None: 41.0}[name]

        with ThreadPoolExecutor(4) as pool, patch.object(qp, "quic_hello", hello):
            facts = qp.collect("youtube.com", "203.0.113.5", submit=pool.submit, cancel=SocketCancel())

        self.assertEqual(
            sorted(asked, key=repr),
            sorted([("203.0.113.5", "youtube.com"), ("203.0.113.5", qp.NEUTRAL_NAME), ("203.0.113.5", None)], key=repr),
        )
        self.assertEqual((facts.real_ms, facts.neutral_ms, facts.nameless_ms, facts.cancelled), (None, 40.0, 41.0, False))


if __name__ == "__main__":
    unittest.main()
