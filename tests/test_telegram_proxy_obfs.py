from __future__ import annotations

import hashlib
import os
import struct
import unittest

from telegram_proxy.proxy.aes_ctr import AesCtrStream, aes_ctr_keystream
from telegram_proxy.proxy.obfs import (
    ABRIDGED,
    DC_FIELD_PLUS,
    DC_FIELD_RANDOM,
    DC_FIELD_SIGNED,
    INTERMEDIATE,
    PADDED,
    ObfsProtocolError,
    PacketReader,
    build_server_header,
    dc_field_value,
    encode_packet,
    parse_plain_client,
    parse_secret_client,
)


def make_client_header(tag: bytes, dc: int, secret: bytes = b"") -> tuple[bytes, AesCtrStream, AesCtrStream]:
    """Заголовок, как его шлёт Telegram Desktop, и шифры клиента (туда, обратно)."""
    while True:
        header = bytearray(os.urandom(64))
        if header[0] != 0xEF and bytes(header[4:8]) != b"\0\0\0\0":
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


def unencrypted_payload(body_len: int) -> bytes:
    return b"\0" * 8 + os.urandom(8) + struct.pack("<I", body_len) + os.urandom(body_len)


def encrypted_payload(blocks: int) -> bytes:
    return os.urandom(8) + os.urandom(16) + os.urandom(16 * blocks)


class ClientHeaderTests(unittest.TestCase):
    def test_plain_client_gives_framing_dc_and_working_ciphers(self) -> None:
        header, to_server, from_server = make_client_header(b"\xef" * 4, 2)
        client = parse_plain_client(header)
        self.assertIsNotNone(client)
        self.assertEqual((client.framing, client.dc, client.is_media), (ABRIDGED, 2, False))

        payload = unencrypted_payload(40)
        self.assertEqual(
            client.cipher.decryptor.update(to_server.update(encode_packet(ABRIDGED, payload))),
            encode_packet(ABRIDGED, payload),
        )
        reply = encode_packet(ABRIDGED, b"\x01\x02\x03\x04")
        self.assertEqual(from_server.update(client.cipher.encryptor.update(reply)), reply)

    def test_secret_client_reads_padded_media_dc(self) -> None:
        secret = bytes.fromhex("00112233445566778899aabbccddeeff")
        header, _to, _back = make_client_header(b"\xdd" * 4, -4, secret)
        client = parse_secret_client(header, secret.hex())
        self.assertEqual((client.framing, client.dc, client.is_media), (PADDED, 4, True))

    def test_wrong_secret_is_rejected(self) -> None:
        secret = bytes.fromhex("00112233445566778899aabbccddeeff")
        header, _to, _back = make_client_header(b"\xdd" * 4, 2, secret)
        self.assertIsNone(parse_secret_client(header, "ff" * 16))
        self.assertIsNone(parse_plain_client(b"\0" * 10))


class ServerHeaderTests(unittest.TestCase):
    def _decrypt_tail(self, header: bytes) -> bytes:
        keystream = aes_ctr_keystream(header[8:40], header[40:56], 64)
        return bytes(a ^ b for a, b in zip(header[56:64], keystream[56:64]))

    def test_dc_field_follows_route_policy_and_tag_is_abridged(self) -> None:
        cases = [
            (DC_FIELD_PLUS, 2, True, 2),
            (DC_FIELD_SIGNED, 2, True, -2),
            (DC_FIELD_SIGNED, 5, False, 5),
        ]
        for policy, dc, media, expected in cases:
            with self.subTest(policy=policy, media=media):
                header, _cipher = build_server_header(dc_field_value(policy, dc, media))
                tail = self._decrypt_tail(header)
                self.assertEqual(tail[:4], b"\xef" * 4)
                self.assertEqual(struct.unpack("<h", tail[4:6])[0], expected)
        self.assertIsNone(dc_field_value(DC_FIELD_RANDOM, 2, False))

    def test_header_never_starts_like_other_protocols(self) -> None:
        for _ in range(300):
            header, _cipher = build_server_header(None)
            self.assertNotEqual(header[0], 0xEF)
            self.assertNotIn(header[:4], {b"HEAD", b"POST", b"GET ", b"OPTI", b"\xee" * 4, b"\xdd" * 4})
            self.assertNotEqual(header[4:8], b"\0\0\0\0")

    def test_server_cipher_matches_what_telegram_decrypts(self) -> None:
        header, cipher = build_server_header(2)
        telegram_in = AesCtrStream(header[8:40], header[40:56])
        telegram_in.update(header)
        packet = encode_packet(ABRIDGED, b"\x05" * 8)
        self.assertEqual(telegram_in.update(cipher.encryptor.update(packet)), packet)
        reverse = header[8:56][::-1]
        telegram_out = AesCtrStream(reverse[:32], reverse[32:48])
        self.assertEqual(cipher.decryptor.update(telegram_out.update(packet)), packet)


class PacketFramingTests(unittest.TestCase):
    def test_abridged_split_across_chunks_and_long_form(self) -> None:
        small = os.urandom(8)
        big = os.urandom(4 * 0x90)
        stream = encode_packet(ABRIDGED, small) + encode_packet(ABRIDGED, big)
        self.assertEqual(stream[1 + 8], 0x7F)
        reader = PacketReader(ABRIDGED)
        packets = []
        for index in range(0, len(stream), 5):
            packets.extend(reader.feed(stream[index:index + 5]))
        self.assertEqual(packets, [small, big])
        self.assertEqual(reader.pending_bytes, 0)

    def test_quick_ack_request_bit_is_dropped(self) -> None:
        payload = os.urandom(12)
        self.assertEqual(PacketReader(ABRIDGED).feed(bytes((0x80 | 3,)) + payload), [payload])
        self.assertEqual(
            PacketReader(INTERMEDIATE).feed(struct.pack("<I", 0x80000000 | 12) + payload),
            [payload],
        )

    def test_padded_padding_is_removed_exactly(self) -> None:
        for payload in (unencrypted_payload(36), encrypted_payload(3), encrypted_payload(1)):
            for _ in range(20):
                with self.subTest(size=len(payload)):
                    self.assertEqual(PacketReader(PADDED).feed(encode_packet(PADDED, payload)), [payload])

    def test_zero_length_is_protocol_error(self) -> None:
        with self.assertRaises(ObfsProtocolError):
            PacketReader(INTERMEDIATE).feed(b"\0\0\0\0")


if __name__ == "__main__":
    unittest.main()
