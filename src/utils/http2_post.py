"""Один запрос POST по HTTP/2 на уже установленном шифрованном соединении.

Часть серверов шифрованного DNS (например, Quad9) отвечает только по HTTP/2,
а в стандартной библиотеке Python его нет. Здесь ровно столько протокола,
сколько нужно для одного короткого запроса и ответа: приветствие, заголовки
запроса, тело, чтение кадров ответа. Ни сжатия заголовков по словарю, ни
нескольких потоков, ни управления окном: ответ DNS заведомо меньше окна по
умолчанию (64 КБ).

Из заголовков ответа читается только код состояния: он всегда идёт первым.
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable

__all__ = ["Http2Error", "post"]

_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

_DATA = 0x0
_HEADERS = 0x1
_RST_STREAM = 0x3
_SETTINGS = 0x4
_PING = 0x6
_GOAWAY = 0x7

_FLAG_END_STREAM = 0x1
_FLAG_ACK = 0x1
_FLAG_END_HEADERS = 0x4
_FLAG_PADDED = 0x8
_FLAG_PRIORITY = 0x20

_STREAM = 1
_MAX_BODY = 64 * 1024

# Готовые строки таблицы заголовков HTTP/2 (RFC 7541, приложение A).
_STATUS_BY_INDEX = {8: 200, 9: 204, 10: 206, 11: 304, 12: 400, 13: 404, 14: 500}
# Сжатые коды цифр (RFC 7541, приложение B): «0–2» по пять бит, «3–9» по шесть.
_DIGIT_BY_5_BITS = {0b00000: "0", 0b00001: "1", 0b00010: "2"}
_DIGIT_BY_6_BITS = {0b011001 + offset: str(3 + offset) for offset in range(7)}


class Http2Error(OSError):
    """Сервер нарушил протокол или сам оборвал обмен."""


def _frame(kind: int, flags: int, stream: int, payload: bytes = b"") -> bytes:
    return struct.pack("!I", len(payload))[1:] + bytes([kind, flags]) + struct.pack("!I", stream) + payload


def _integer(value: int, prefix_bits: int, first: int = 0) -> bytes:
    limit = (1 << prefix_bits) - 1
    if value < limit:
        return bytes([first | value])
    out = bytearray([first | limit])
    value -= limit
    while value >= 128:
        out.append(value % 128 + 128)
        value //= 128
    out.append(value)
    return bytes(out)


def _text(value: str) -> bytes:
    raw = value.encode("ascii")
    return _integer(len(raw), 7) + raw


def _literal(name_index: int, value: str) -> bytes:
    """Заголовок с именем из готовой таблицы и значением открытым текстом."""
    return _integer(name_index, 4) + _text(value)


def _request_headers(authority: str, path: str, content_type: str, length: int) -> bytes:
    return (
        b"\x83"  # :method POST
        + b"\x87"  # :scheme https
        + _literal(4, path)  # :path
        + _literal(1, authority)  # :authority
        + _literal(31, content_type)  # content-type
        + _literal(19, content_type)  # accept
        + _literal(28, str(length))  # content-length
    )


def _read_integer(block: bytes, position: int, prefix_bits: int) -> tuple[int, int]:
    limit = (1 << prefix_bits) - 1
    value = block[position] & limit
    position += 1
    if value < limit:
        return value, position
    shift = 0
    while True:
        byte = block[position]
        position += 1
        value += (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, position


def _decode_digits(raw: bytes) -> str:
    bits = "".join(f"{byte:08b}" for byte in raw)
    digits = ""
    position = 0
    while len(bits) - position >= 5:
        digit = _DIGIT_BY_5_BITS.get(int(bits[position : position + 5], 2))
        if digit is not None:
            position += 5
        elif len(bits) - position >= 6:
            digit = _DIGIT_BY_6_BITS.get(int(bits[position : position + 6], 2))
            position += 6
        if digit is None:
            break
        digits += digit
    return digits


def read_status(block: bytes) -> int | None:
    """Код состояния из начала блока заголовков ответа или None, если он записан иначе."""
    try:
        position = 0
        # Сервер может начать с сообщения о размере своего словаря.
        while block[position] & 0xE0 == 0x20:
            _size, position = _read_integer(block, position, 5)
        first = block[position]
        if first & 0x80:
            return _STATUS_BY_INDEX.get(first & 0x7F)
        index, position = _read_integer(block, position, 6 if first & 0x40 else 4)
        if index not in _STATUS_BY_INDEX:
            return None
        compressed = bool(block[position] & 0x80)
        size, position = _read_integer(block, position, 7)
        raw = block[position : position + size]
        text = _decode_digits(raw) if compressed else raw.decode("ascii")
        return int(text) if len(text) == 3 else None
    except (IndexError, ValueError):
        return None


def _recv_exact(sock: socket.socket, size: int, remaining: Callable[[], float]) -> bytes:
    data = b""
    while len(data) < size:
        sock.settimeout(remaining())
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise Http2Error("сервер закрыл соединение")
        data += chunk
    return data


def _strip_padding(payload: bytes, flags: int) -> bytes:
    if not flags & _FLAG_PADDED:
        return payload
    if not payload or payload[0] >= len(payload):
        raise Http2Error("неверное выравнивание кадра")
    return payload[1 : len(payload) - payload[0]]


def post(
    sock: socket.socket,
    *,
    authority: str,
    path: str,
    body: bytes,
    content_type: str,
    remaining: Callable[[], float],
) -> tuple[int | None, bytes]:
    """Отправляет запрос и возвращает (код состояния или None, тело ответа).

    ``remaining`` — сколько секунд осталось на весь обмен; когда время вышло,
    она сама бросает ``socket.timeout``.
    """
    sock.settimeout(remaining())
    sock.sendall(
        _PREFACE
        + _frame(_SETTINGS, 0, 0)
        + _frame(_HEADERS, _FLAG_END_HEADERS, _STREAM, _request_headers(authority, path, content_type, len(body)))
        + _frame(_DATA, _FLAG_END_STREAM, _STREAM, body)
    )

    status: int | None = None
    got_headers = False
    received = b""
    while True:
        header = _recv_exact(sock, 9, remaining)
        size = int.from_bytes(header[:3], "big")
        kind, flags = header[3], header[4]
        stream = struct.unpack("!I", header[5:])[0] & 0x7FFFFFFF
        payload = _recv_exact(sock, size, remaining) if size else b""

        if kind == _SETTINGS and not flags & _FLAG_ACK:
            sock.settimeout(remaining())
            sock.sendall(_frame(_SETTINGS, _FLAG_ACK, 0))
        elif kind == _PING and not flags & _FLAG_ACK:
            sock.settimeout(remaining())
            sock.sendall(_frame(_PING, _FLAG_ACK, 0, payload))
        elif kind == _GOAWAY:
            raise Http2Error("сервер завершил соединение")
        elif stream != _STREAM:
            continue
        elif kind == _RST_STREAM:
            raise Http2Error("сервер отклонил запрос")
        elif kind == _HEADERS:
            block = _strip_padding(payload, flags)
            if flags & _FLAG_PRIORITY:
                block = block[5:]
            # Завершающие заголовки после тела кода состояния не несут.
            if not got_headers:
                status = read_status(block)
                got_headers = True
            if flags & _FLAG_END_STREAM:
                return status, received
        elif kind == _DATA:
            received += _strip_padding(payload, flags)
            if len(received) > _MAX_BODY:
                raise Http2Error("ответ слишком большой")
            if flags & _FLAG_END_STREAM:
                return status, received
