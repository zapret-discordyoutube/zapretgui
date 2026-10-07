"""Первый пакет QUIC (Initial) с именем сайта — для проверки, пропускают ли его.

QUIC идёт по UDP на порт 443; на нём браузеры открывают YouTube и многие
другие сайты. Первый пакет зашифрован, но ключ выводится из открытых данных
самого пакета (RFC 9001, раздел 5.2), поэтому фильтр по дороге может его
расшифровать и прочитать имя сайта. Чтобы проверить, блокируют ли QUIC по
имени, нужен такой же настоящий пакет: с правильным шифрованием и с
приветствием TLS 1.3 внутри.

Шифрование здесь своё, на чистом Python: в стандартной библиотеке AES нет, а
для одного пакета скорость не важна. Нужно только зашифровать — расшифровывать
ответ не требуется: ответом считается любой пакет сервера на наше соединение.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct

__all__ = [
    "InitialPacket",
    "aes128_encrypt_block",
    "aes128_gcm_seal",
    "build_initial",
    "initial_keys",
    "is_reply_to",
]

QUIC_V1 = 0x00000001
# RFC 9001, 5.2: из этой соли и номера соединения выводятся ключи первого пакета.
_INITIAL_SALT = bytes.fromhex("38762cf7f55934b34d179ae6a4c80cadccbb7f0a")
# Сервер отвечает только на первый пакет не короче 1200 байт (RFC 9000, 14.1).
_MIN_DATAGRAM = 1200
_PACKET_NUMBER_SIZE = 4
_TAG_SIZE = 16


# ---------------------------------------------------------------------------
# AES-128: шифрование одного блока (FIPS 197)
# ---------------------------------------------------------------------------


def _build_sbox() -> bytes:
    """Таблица замены вычисляется, а не вписывается: в 256 числах легко ошибиться."""
    sbox = [0] * 256
    p = q = 1
    while True:
        # p умножается на 3, q делится на 3 в поле GF(2^8): q — обратный к p.
        p = (p ^ (p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        value = q ^ (q << 1 | q >> 7) ^ (q << 2 | q >> 6) ^ (q << 3 | q >> 5) ^ (q << 4 | q >> 4)
        sbox[p] = (value ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return bytes(sbox)


_SBOX = _build_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(value: int) -> int:
    return ((value << 1) ^ 0x1B) & 0xFF if value & 0x80 else value << 1


def _expand_key(key: bytes) -> list[bytes]:
    if len(key) != 16:
        raise ValueError("нужен ключ AES-128 из 16 байт")
    words = [key[i : i + 4] for i in range(0, 16, 4)]
    for index in range(4, 44):
        word = words[index - 1]
        if index % 4 == 0:
            rotated = word[1:] + word[:1]
            word = bytes(_SBOX[byte] for byte in rotated)
            word = bytes([word[0] ^ _RCON[index // 4 - 1]]) + word[1:]
        words.append(bytes(a ^ b for a, b in zip(words[index - 4], word)))
    return [b"".join(words[i : i + 4]) for i in range(0, 44, 4)]


def _encrypt_with_round_keys(round_keys: list[bytes], block: bytes) -> bytes:
    state = bytes(a ^ b for a, b in zip(block, round_keys[0]))
    for round_index in range(1, 11):
        # Замена байтов и сдвиг строк: байт [строка, столбец] лежит в state[4*столбец + строка].
        state = bytes(_SBOX[state[(4 * (column + row) + row) % 16]] for column in range(4) for row in range(4))
        if round_index != 10:
            mixed = bytearray(16)
            for column in range(4):
                a = state[4 * column : 4 * column + 4]
                total = a[0] ^ a[1] ^ a[2] ^ a[3]
                for row in range(4):
                    mixed[4 * column + row] = a[row] ^ total ^ _xtime(a[row] ^ a[(row + 1) % 4])
            state = bytes(mixed)
        state = bytes(a ^ b for a, b in zip(state, round_keys[round_index]))
    return state


def aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    if len(block) != 16:
        raise ValueError("блок AES — ровно 16 байт")
    return _encrypt_with_round_keys(_expand_key(key), block)


# ---------------------------------------------------------------------------
# AES-128-GCM: шифрование с проверочной меткой (NIST SP 800-38D)
# ---------------------------------------------------------------------------

_GCM_REDUCTION = 0xE1 << 120


def _gf_multiply(x: int, y: int) -> int:
    result = 0
    for bit in range(127, -1, -1):
        if (x >> bit) & 1:
            result ^= y
        y = (y >> 1) ^ _GCM_REDUCTION if y & 1 else y >> 1
    return result


def _ghash(h: int, aad: bytes, ciphertext: bytes) -> int:
    def blocks(data: bytes):
        for offset in range(0, len(data), 16):
            yield int.from_bytes(data[offset : offset + 16].ljust(16, b"\x00"), "big")

    value = 0
    for block in (*blocks(aad), *blocks(ciphertext), (len(aad) * 8) << 64 | len(ciphertext) * 8):
        value = _gf_multiply(value ^ block, h)
    return value


def aes128_gcm_seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """Шифротекст и 16-байтовая метка одним куском. ``nonce`` — 12 байт."""
    if len(nonce) != 12:
        raise ValueError("нужен nonce из 12 байт")
    round_keys = _expand_key(key)
    h = int.from_bytes(_encrypt_with_round_keys(round_keys, bytes(16)), "big")
    ciphertext = bytearray()
    for index in range(0, len(plaintext), 16):
        counter = nonce + struct.pack("!I", index // 16 + 2)
        stream = _encrypt_with_round_keys(round_keys, counter)
        ciphertext.extend(a ^ b for a, b in zip(plaintext[index : index + 16], stream))
    tag_mask = _encrypt_with_round_keys(round_keys, nonce + b"\x00\x00\x00\x01")
    tag = _ghash(h, aad, bytes(ciphertext)) ^ int.from_bytes(tag_mask, "big")
    return bytes(ciphertext) + tag.to_bytes(16, "big")


# ---------------------------------------------------------------------------
# Ключи первого пакета (RFC 9001, 5.2)
# ---------------------------------------------------------------------------


def _hkdf_expand_label(secret: bytes, label: str, length: int) -> bytes:
    full = b"tls13 " + label.encode("ascii")
    info = struct.pack("!H", length) + bytes([len(full)]) + full + b"\x00"
    return hmac.new(secret, info + b"\x01", hashlib.sha256).digest()[:length]


def initial_keys(destination_id: bytes) -> tuple[bytes, bytes, bytes]:
    """(ключ, вектор, ключ защиты заголовка) клиента для первого пакета."""
    initial = hmac.new(_INITIAL_SALT, destination_id, hashlib.sha256).digest()
    client = _hkdf_expand_label(initial, "client in", 32)
    return (
        _hkdf_expand_label(client, "quic key", 16),
        _hkdf_expand_label(client, "quic iv", 12),
        _hkdf_expand_label(client, "quic hp", 16),
    )


# ---------------------------------------------------------------------------
# Приветствие TLS 1.3 и сам пакет
# ---------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    if value < 0x40:
        return bytes([value])
    if value < 0x4000:
        return struct.pack("!H", value | 0x4000)
    if value < 0x40000000:
        return struct.pack("!I", value | 0x80000000)
    return struct.pack("!Q", value | 0xC000000000000000)


def _extension(kind: int, body: bytes) -> bytes:
    return struct.pack("!HH", kind, len(body)) + body


def _transport_parameters(source_id: bytes) -> bytes:
    def parameter(identifier: int, value: bytes) -> bytes:
        return _varint(identifier) + _varint(len(value)) + value

    return b"".join(
        (
            parameter(0x01, _varint(30_000)),  # сколько ждать без данных, мс
            parameter(0x03, _varint(1472)),  # наибольший пакет
            parameter(0x04, _varint(1_048_576)),  # сколько данных готовы принять всего
            parameter(0x05, _varint(262_144)),
            parameter(0x06, _varint(262_144)),
            parameter(0x07, _varint(262_144)),
            parameter(0x08, _varint(100)),  # сколько потоков может открыть сервер
            parameter(0x09, _varint(100)),
            parameter(0x0F, source_id),  # наш номер соединения — обязателен
        )
    )


def _client_hello(server_name: str | None, source_id: bytes) -> bytes:
    extensions = b""
    if server_name:
        name = server_name.encode("idna")
        entry = b"\x00" + struct.pack("!H", len(name)) + name
        extensions += _extension(0x0000, struct.pack("!H", len(entry)) + entry)
    groups = struct.pack("!HH", 0x001D, 0x0017)
    extensions += _extension(0x000A, struct.pack("!H", len(groups)) + groups)
    signatures = struct.pack("!8H", 0x0403, 0x0804, 0x0401, 0x0503, 0x0805, 0x0501, 0x0806, 0x0601)
    extensions += _extension(0x000D, struct.pack("!H", len(signatures)) + signatures)
    protocol = b"\x02h3"
    extensions += _extension(0x0010, struct.pack("!H", len(protocol)) + protocol)
    extensions += _extension(0x002B, b"\x02\x03\x04")  # только TLS 1.3
    extensions += _extension(0x002D, b"\x01\x01")
    # Открытый ключ для обмена: достаточно случайного — расшифровывать ответ мы не будем.
    share = struct.pack("!HH", 0x001D, 32) + os.urandom(32)
    extensions += _extension(0x0033, struct.pack("!H", len(share)) + share)
    extensions += _extension(0x0039, _transport_parameters(source_id))

    body = (
        b"\x03\x03"
        + os.urandom(32)
        + b"\x00"  # в QUIC номер сеанса пустой
        + struct.pack("!H", 6)
        + b"\x13\x01\x13\x02\x13\x03"
        + b"\x01\x00"
        + struct.pack("!H", len(extensions))
        + extensions
    )
    return b"\x01" + struct.pack("!I", len(body))[1:] + body


class InitialPacket:
    """Готовый к отправке пакет и номер соединения, по которому узнаётся ответ."""

    __slots__ = ("datagram", "destination_id", "source_id")

    def __init__(self, datagram: bytes, destination_id: bytes, source_id: bytes) -> None:
        self.datagram = datagram
        self.destination_id = destination_id
        self.source_id = source_id


def build_initial(
    server_name: str | None,
    *,
    destination_id: bytes | None = None,
    source_id: bytes | None = None,
) -> InitialPacket:
    """Первый пакет QUIC версии 1 с приветствием для ``server_name`` (None — без имени)."""
    destination_id = destination_id if destination_id is not None else os.urandom(8)
    source_id = source_id if source_id is not None else os.urandom(8)
    key, iv, header_key = initial_keys(destination_id)

    hello = _client_hello(server_name, source_id)
    frame = b"\x06" + _varint(0) + _varint(len(hello)) + hello
    header_start = (
        bytes([0xC0 | (_PACKET_NUMBER_SIZE - 1)])
        + struct.pack("!I", QUIC_V1)
        + bytes([len(destination_id)])
        + destination_id
        + bytes([len(source_id)])
        + source_id
        + _varint(0)  # без жетона
    )
    # Длина пишется двумя байтами, номер пакета — четырьмя.
    fixed = len(header_start) + 2 + _PACKET_NUMBER_SIZE + _TAG_SIZE
    payload = frame + bytes(max(0, _MIN_DATAGRAM - fixed - len(frame)))
    length = _PACKET_NUMBER_SIZE + len(payload) + _TAG_SIZE
    packet_number = bytes(_PACKET_NUMBER_SIZE)
    header = header_start + struct.pack("!H", length | 0x4000) + packet_number

    nonce = bytes(a ^ b for a, b in zip(iv, packet_number.rjust(12, b"\x00")))
    sealed = aes128_gcm_seal(key, nonce, payload, header)

    # Защита заголовка: по образцу из шифротекста прячутся младшие биты первого
    # байта и номер пакета (RFC 9001, 5.4).
    mask = aes128_encrypt_block(header_key, sealed[:16])
    protected = bytearray(header)
    protected[0] ^= mask[0] & 0x0F
    number_offset = len(header) - _PACKET_NUMBER_SIZE
    for index in range(_PACKET_NUMBER_SIZE):
        protected[number_offset + index] ^= mask[1 + index]
    return InitialPacket(bytes(protected) + sealed, destination_id, source_id)


def is_reply_to(datagram: bytes, packet: InitialPacket) -> bool:
    """Пакет сервера на наше соединение: длинный заголовок и наш номер получателем.

    Годится любой ответ — продолжение рукопожатия, отказ, предложение другой
    версии или просьба повторить: раз сервер ответил, наш пакет до него дошёл.
    """
    if len(datagram) < 7 or not datagram[0] & 0x80:
        return False
    size = datagram[5]
    return datagram[6 : 6 + size] == packet.source_id
