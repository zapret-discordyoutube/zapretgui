"""Obfuscated2 для прокси: разбор потока клиента и сборка нового потока к Telegram.

Telegram Desktop шлёт прокси свой поток: через SOCKS5 — abridged (0xef) без
секрета, через MTProxy с секретом dd/ee — padded intermediate (0xdd). Прокси
расшифровывает его, режет на MTProto-пакеты и собирает для каждого маршрута
новый заголовок obfuscated2 уже без секрета и всегда в abridged:

* релей kwsN пропускает в кадре только первый пакет, поэтому один пакет — один
  WS-кадр, а заголовок едет вместе с первым пакетом;
* номер DC в байтах 60..61 зависит от маршрута: релею он не нужен (DC задаёт
  имя хоста), фронту Cloudflare нужен +dc, туннелю — ±dc.

Правила сверены с ZaStoGram (jni/tgnet/Connection.cpp) и tdesktop
(mtproto/transport/connection_tcp.cpp).
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass

from telegram_proxy.proxy.aes_ctr import AesCtrStream, aes_ctr_keystream


HEADER_LEN = 64

TAG_ABRIDGED = b"\xef\xef\xef\xef"
TAG_INTERMEDIATE = b"\xee\xee\xee\xee"
TAG_PADDED = b"\xdd\xdd\xdd\xdd"

ABRIDGED = "abridged"
INTERMEDIATE = "intermediate"
PADDED = "padded"

FRAMING_BY_TAG = {
    TAG_ABRIDGED: ABRIDGED,
    TAG_INTERMEDIATE: INTERMEDIATE,
    TAG_PADDED: PADDED,
}

# Больше MTProto-пакет быть не может (tdesktop: kPacketSizeMax).
MAX_PACKET_LEN = 64 * 1024 * 1024

# Начала заголовка, которые сервер принял бы за другой протокол.
_FORBIDDEN_STARTS = frozenset(
    {
        b"HEAD",
        b"POST",
        b"GET ",
        b"OPTI",
        TAG_INTERMEDIATE,
        TAG_PADDED,
        b"\x16\x03\x01\x02",
    }
)

# Как заполнять байты 60..61 заголовка.
DC_FIELD_RANDOM = "random"  # релей kwsN: DC задаёт имя хоста
DC_FIELD_PLUS = "plus"  # фронты Cloudflare: всегда +dc, даже для медиа
DC_FIELD_SIGNED = "signed"  # туннель и прямой TCP: -dc для медиа


class ObfsProtocolError(Exception):
    """Поток клиента или сервера не похож на MTProto."""


@dataclass(slots=True)
class StreamCipher:
    """Пара шифров одной стороны: чем расшифровывать входящее и шифровать исходящее."""

    decryptor: AesCtrStream
    encryptor: AesCtrStream


@dataclass(slots=True)
class ClientSide:
    """Разобранный заголовок клиента: формат пакетов, DC и шифры."""

    framing: str
    dc: int
    is_media: bool
    cipher: StreamCipher


def _pair_from_prekey(prekey_iv: bytes, secret: bytes = b"") -> tuple[AesCtrStream, AesCtrStream]:
    """Шифр «от инициатора» и шифр «к инициатору» по 48 байтам заголовка."""
    forward_key = prekey_iv[:32]
    forward_iv = prekey_iv[32:48]
    reverse = prekey_iv[::-1]
    backward_key = reverse[:32]
    backward_iv = reverse[32:48]
    if secret:
        forward_key = hashlib.sha256(forward_key + secret).digest()
        backward_key = hashlib.sha256(backward_key + secret).digest()
    forward = AesCtrStream(forward_key, forward_iv)
    backward = AesCtrStream(backward_key, backward_iv)
    return forward, backward


def _parse_client(init: bytes, secret: bytes) -> ClientSide | None:
    if len(init or b"") < HEADER_LEN:
        return None
    prekey_iv = bytes(init[8:56])
    from_client, to_client = _pair_from_prekey(prekey_iv, secret)
    plain = from_client.update(bytes(init[:HEADER_LEN]))
    framing = FRAMING_BY_TAG.get(bytes(plain[56:60]))
    if framing is None:
        return None
    dc_raw = struct.unpack("<h", plain[60:62])[0]
    return ClientSide(
        framing=framing,
        dc=abs(dc_raw),
        is_media=dc_raw < 0,
        cipher=StreamCipher(decryptor=from_client, encryptor=to_client),
    )


def parse_plain_client(init: bytes) -> ClientSide | None:
    """Заголовок клиента SOCKS5: ключи лежат прямо в заголовке."""
    return _parse_client(init, b"")


def parse_secret_client(init: bytes, secret_hex: str) -> ClientSide | None:
    """Заголовок клиента MTProxy: ключи смешаны с секретом."""
    try:
        secret = bytes.fromhex(str(secret_hex or ""))
    except ValueError:
        return None
    if len(secret) != 16:
        return None
    return _parse_client(init, secret)


def dc_field_value(policy: str, dc: int, is_media: bool) -> int | None:
    if policy == DC_FIELD_RANDOM:
        return None
    if policy == DC_FIELD_PLUS:
        return int(dc)
    return -int(dc) if is_media else int(dc)


def build_server_header(dc_value: int | None) -> tuple[bytes, StreamCipher]:
    """Новый заголовок к Telegram (abridged, без секрета) и шифры этой связи."""
    while True:
        header = bytearray(os.urandom(HEADER_LEN))
        if header[0] == 0xEF:
            continue
        if bytes(header[:4]) in _FORBIDDEN_STARTS:
            continue
        if bytes(header[4:8]) == b"\x00\x00\x00\x00":
            continue
        break
    prekey_iv = bytes(header[8:56])
    dc_bytes = os.urandom(2) if dc_value is None else struct.pack("<h", int(dc_value))
    tail_plain = TAG_ABRIDGED + dc_bytes + os.urandom(2)
    keystream = aes_ctr_keystream(prekey_iv[:32], prekey_iv[32:48], HEADER_LEN)
    header[56:64] = bytes(a ^ b for a, b in zip(tail_plain, keystream[56:64]))
    to_server, from_server = _pair_from_prekey(prekey_iv)
    to_server.update(b"\x00" * HEADER_LEN)
    return bytes(header), StreamCipher(decryptor=from_server, encryptor=to_server)


def _strip_padding(data: bytes) -> bytes:
    """Убирает случайную добивку padded intermediate по устройству MTProto.

    Незашифрованный пакет: auth_key_id=0 (8) + msg_id (8) + длина (4) + тело.
    Зашифрованный: auth_key_id (8) + msg_key (16) + данные кратно 16.
    Добивка всегда меньше 16 байт, поэтому длина восстанавливается однозначно.
    """
    size = len(data)
    if size >= 20 and data[:8] == b"\x00" * 8:
        exact = 20 + struct.unpack_from("<I", data, 16)[0]
        if exact <= size:
            return data[:exact]
        raise ObfsProtocolError("padded: длина тела больше пакета")
    exact = size - ((size - 8) % 16)
    if exact < 24:
        # Короткий служебный пакет (например, код ошибки транспорта).
        return data[: size - (size % 4)]
    return data[:exact]


class PacketReader:
    """Режет расшифрованный поток на MTProto-пакеты (без длины и добивки)."""

    __slots__ = ("_framing", "_buf")

    def __init__(self, framing: str):
        if framing not in (ABRIDGED, INTERMEDIATE, PADDED):
            raise ValueError(f"unknown framing: {framing}")
        self._framing = framing
        self._buf = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._buf)

    def feed(self, plain: bytes) -> list[bytes]:
        if plain:
            self._buf.extend(plain)
        packets: list[bytes] = []
        while True:
            packet = self._next()
            if packet is None:
                break
            packets.append(packet)
        return packets

    def _next(self) -> bytes | None:
        buf = self._buf
        if not buf:
            return None
        if self._framing == ABRIDGED:
            first = buf[0] & 0x7F
            if first == 0x7F:
                if len(buf) < 4:
                    return None
                size = int.from_bytes(buf[1:4], "little") * 4
                head = 4
            else:
                size = first * 4
                head = 1
        else:
            if len(buf) < 4:
                return None
            size = struct.unpack_from("<I", buf, 0)[0] & 0x7FFFFFFF
            head = 4
        if size <= 0 or size > MAX_PACKET_LEN:
            raise ObfsProtocolError(f"{self._framing}: неверная длина пакета {size}")
        if len(buf) < head + size:
            return None
        payload = bytes(buf[head:head + size])
        del buf[:head + size]
        if self._framing == PADDED:
            payload = _strip_padding(payload)
        return payload


def encode_packet(framing: str, payload: bytes) -> bytes:
    """Длина + пакет в нужном формате. Бит quick ack никогда не ставится."""
    size = len(payload)
    if framing == ABRIDGED:
        if size % 4:
            raise ObfsProtocolError("abridged: длина пакета не кратна 4")
        words = size // 4
        if words < 0x7F:
            return bytes((words,)) + payload
        return b"\x7f" + words.to_bytes(3, "little") + payload
    if framing == INTERMEDIATE:
        return struct.pack("<I", size) + payload
    if framing == PADDED:
        padding = os.urandom(os.urandom(1)[0] % 16)
        return struct.pack("<I", size + len(padding)) + payload + padding
    raise ValueError(f"unknown framing: {framing}")


def is_http_transport(data: bytes) -> bool:
    """Первые байты похожи на HTTP-транспорт Telegram (порт 80), а не на MTProto."""
    return (
        data[:5] == b"POST "
        or data[:4] == b"GET "
        or data[:5] == b"HEAD "
        or data[:8] == b"OPTIONS "
    )


__all__ = [
    "ABRIDGED",
    "ClientSide",
    "DC_FIELD_PLUS",
    "DC_FIELD_RANDOM",
    "DC_FIELD_SIGNED",
    "HEADER_LEN",
    "INTERMEDIATE",
    "ObfsProtocolError",
    "PADDED",
    "PacketReader",
    "StreamCipher",
    "build_server_header",
    "dc_field_value",
    "encode_packet",
    "is_http_transport",
    "parse_plain_client",
    "parse_secret_client",
]
