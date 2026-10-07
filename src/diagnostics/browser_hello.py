"""Приветствие «как у Chrome»: проходит ли оно до сервера.

Обычная проверка открывает сайт средствами Python. Её приветствие (первый пакет
шифрованного соединения, ClientHello) не похоже на браузерное: другой набор
шифров и расширений, втрое короче. Фильтр провайдера умеет различать
программы по этому «почерку» и резать только браузерный — тогда проверка
говорит «открывается», а в Chrome сайт висит.

Здесь собирается приветствие с тем же составом, что у настольного Chrome:
те же шифры, те же шестнадцать расширений, ключ постквантового обмена
(из-за него приветствие занимает два пакета, как у настоящего браузера) и
случайные «пустышки» GREASE. Отправляем его и смотрим только одно: пришёл ли
от сервера ответ.

Отпечаток JA4 этого приветствия — ``t13d1516h2_8daaf6152771_d8a2da3f94cd``
(``BLOCKED_JA4``): тот самый, про который известно, что фильтр режет его на
адресах датацентров. Chrome новее (по сообщениям — со 150-й версии), Firefox и
curl имеют другие отпечатки и под этот блок не попадают — поэтому вывод звучит «может не открываться в Chrome»,
а не «в браузере не откроется».

Чего проверка НЕ делает: соединение дальше приветствия не идёт. Блокировку,
которая срабатывает позже (обрыв после 16 КБ именно для браузерного почерка),
она не увидит.

Здесь только сбор приветствия и один факт «что пришло в ответ». Вывод делает
``diagnostics.protocol_probe``: он сравнивает этот ответ с обычным приветствием
к тому же адресу.
"""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import time
from collections.abc import Callable

from diagnostics.block_cause import (
    HELLO_ALERT,
    HELLO_CANCELLED,
    HELLO_CONNECT,
    HELLO_OK,
    HELLO_GARBAGE,
    HELLO_RESET,
    HELLO_TIMEOUT,
    HELLO_TIMEOUT_S,
    HelloResult,
)
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = ["BLOCKED_JA4", "CHROME_CIPHERS", "CHROME_EXTENSIONS", "build_hello", "ja4", "name_offset", "parse_hello", "send_hello", "send_parts", "split_record"]

# Отпечаток настольного Chrome, который режет фильтр (описан на wiki.zapret.moe).
BLOCKED_JA4 = "t13d1516h2_8daaf6152771_d8a2da3f94cd"

# Шифры настольного Chrome по порядку (без GREASE).
CHROME_CIPHERS = (
    0x1301, 0x1302, 0x1303, 0xC02B, 0xC02F, 0xC02C, 0xC030, 0xCCA9,
    0xCCA8, 0xC013, 0xC014, 0x009C, 0x009D, 0x002F, 0x0035,
)  # fmt: skip

EXT_SNI = 0x0000
EXT_STATUS_REQUEST = 0x0005
EXT_GROUPS = 0x000A
EXT_POINT_FORMATS = 0x000B
EXT_SIGNATURES = 0x000D
EXT_ALPN = 0x0010
EXT_SCT = 0x0012
EXT_MASTER_SECRET = 0x0017
EXT_COMPRESS_CERT = 0x001B
EXT_SESSION_TICKET = 0x0023
EXT_VERSIONS = 0x002B
EXT_PSK_MODES = 0x002D
EXT_KEY_SHARE = 0x0033
EXT_ALPS = 0x44CD
EXT_ECH = 0xFE0D
EXT_RENEGOTIATION = 0xFF01

# Шестнадцать расширений Chrome (без двух GREASE). Порядок браузер перемешивает сам.
CHROME_EXTENSIONS = (
    EXT_SNI, EXT_MASTER_SECRET, EXT_RENEGOTIATION, EXT_GROUPS, EXT_POINT_FORMATS, EXT_SESSION_TICKET,
    EXT_ALPN, EXT_STATUS_REQUEST, EXT_SIGNATURES, EXT_SCT, EXT_KEY_SHARE, EXT_PSK_MODES,
    EXT_VERSIONS, EXT_COMPRESS_CERT, EXT_ALPS, EXT_ECH,
)  # fmt: skip

GROUP_X25519_MLKEM768 = 0x11EC
GROUP_X25519 = 0x001D
_GROUPS = (GROUP_X25519_MLKEM768, GROUP_X25519, 0x0017, 0x0018)
_SIGNATURES = (0x0403, 0x0804, 0x0401, 0x0503, 0x0805, 0x0501, 0x0806, 0x0601)

_MLKEM_Q = 3329
_MLKEM_COEFFS = 3 * 256

_RECORD_HANDSHAKE = 22
_RECORD_ALERT = 21
_HANDSHAKE_SERVER_HELLO = 2

Random = Callable[[int], bytes]


def _grease(byte: int) -> int:
    """Значение-пустышка вида 0x?A?A: сервер обязан его пропустить."""
    nibble = byte >> 4
    return (nibble << 12) | 0x0A00 | (nibble << 4) | 0x0A


def _vec8(data: bytes) -> bytes:
    return bytes([len(data)]) + data


def _vec16(data: bytes) -> bytes:
    return struct.pack("!H", len(data)) + data


def _u16s(values) -> bytes:
    return b"".join(struct.pack("!H", value) for value in values)


def _mlkem_public_key(rnd: Random) -> bytes:
    """Правдоподобный открытый ключ ML-KEM-768: 1184 байта.

    Сервер проверяет, что каждое из 768 чисел ключа меньше 3329, — случайные
    байты он отверг бы. Числа упакованы по 12 бит, в конце 32 байта зерна.
    """
    raw = rnd(_MLKEM_COEFFS * 2)
    packed = bytearray()
    for index in range(0, _MLKEM_COEFFS, 2):
        first = int.from_bytes(raw[index * 2 : index * 2 + 2], "big") % _MLKEM_Q
        second = int.from_bytes(raw[index * 2 + 2 : index * 2 + 4], "big") % _MLKEM_Q
        packed += bytes((first & 0xFF, (first >> 8) | ((second & 0x0F) << 4), second >> 4))
    return bytes(packed) + rnd(32)


def _extension(kind: int, data: bytes) -> bytes:
    return struct.pack("!HH", kind, len(data)) + data


def build_hello(host: str, *, rnd: Random = os.urandom, post_quantum: bool = True, ech: bool = True) -> bytes:
    """Запись TLS с приветствием Chrome для сайта ``host``.

    ``post_quantum=False`` — без постквантового ключа: приветствие втрое короче и
    умещается в один пакет (нужно пробам, которые сами решают, где его резать).
    ``ech=False`` — без расширения ECH. Оба варианта меняют отпечаток: это уже не
    «почерк Chrome», а просто корректное приветствие.
    """
    seeds = rnd(5)
    grease_cipher, grease_group, grease_first, grease_last, grease_version = (_grease(byte) for byte in seeds)
    if grease_last == grease_first:
        grease_last = _grease((seeds[3] + 0x10) & 0xFF)

    name = host.encode("idna")
    key_shares = (
        struct.pack("!HH", grease_group, 1)
        + b"\x00"
        + (struct.pack("!H", GROUP_X25519_MLKEM768) + _vec16(_mlkem_public_key(rnd) + rnd(32)) if post_quantum else b"")
        + struct.pack("!H", GROUP_X25519)
        + _vec16(rnd(32))
    )
    groups = _GROUPS if post_quantum else _GROUPS[1:]
    ech_body = (
        b"\x00"  # внешнее приветствие
        + struct.pack("!HH", 0x0001, 0x0001)  # HKDF-SHA256, AES-128-GCM
        + rnd(1)
        + _vec16(rnd(32))
        + _vec16(rnd(144 + 32 * (rnd(1)[0] % 4)))
    )
    bodies = {
        EXT_SNI: _vec16(b"\x00" + _vec16(name)),
        EXT_MASTER_SECRET: b"",
        EXT_RENEGOTIATION: b"\x00",
        EXT_GROUPS: _vec16(_u16s((grease_group, *groups))),
        EXT_POINT_FORMATS: _vec8(b"\x00"),
        EXT_SESSION_TICKET: b"",
        EXT_ALPN: _vec16(_vec8(b"h2") + _vec8(b"http/1.1")),
        EXT_STATUS_REQUEST: b"\x01" + b"\x00\x00" + b"\x00\x00",
        EXT_SIGNATURES: _vec16(_u16s(_SIGNATURES)),
        EXT_SCT: b"",
        EXT_KEY_SHARE: _vec16(key_shares),
        EXT_PSK_MODES: _vec8(b"\x01"),
        EXT_VERSIONS: _vec8(_u16s((grease_version, 0x0304, 0x0303))),
        EXT_COMPRESS_CERT: _vec8(_u16s((0x0002,))),  # brotli
        EXT_ALPS: _vec16(_vec8(b"h2")),
        EXT_ECH: ech_body,
    }
    # Chrome перемешивает расширения в каждом соединении; GREASE стоят по краям.
    order = [kind for kind in CHROME_EXTENSIONS if ech or kind != EXT_ECH]
    shuffle = rnd(len(order))
    for index in range(len(order) - 1, 0, -1):
        other = shuffle[index] % (index + 1)
        order[index], order[other] = order[other], order[index]
    extensions = (
        _extension(grease_first, b"")
        + b"".join(_extension(kind, bodies[kind]) for kind in order)
        + _extension(grease_last, b"\x00")
    )

    body = (
        b"\x03\x03"
        + rnd(32)
        + _vec8(rnd(32))  # идентификатор сеанса: Chrome всегда шлёт 32 байта
        + _vec16(_u16s((grease_cipher, *CHROME_CIPHERS)))
        + _vec8(b"\x00")
        + _vec16(extensions)
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return bytes((_RECORD_HANDSHAKE, 0x03, 0x01)) + _vec16(handshake)


def parse_hello(record: bytes) -> tuple[tuple[int, ...], tuple[int, ...], str]:
    """Разбор собранного приветствия: (шифры, расширения, имя сайта). GREASE отброшены.

    Нужен, чтобы тест сверял состав приветствия с составом Chrome.
    """

    def real(value: int) -> bool:
        return not ((value & 0x0F0F) == 0x0A0A and (value >> 8) == (value & 0xFF))

    body = record[9:]
    offset = 2 + 32
    offset += 1 + body[offset]
    size = struct.unpack_from("!H", body, offset)[0]
    ciphers = struct.unpack_from(f"!{size // 2}H", body, offset + 2)
    offset += 2 + size
    offset += 1 + body[offset]
    end = offset + 2 + struct.unpack_from("!H", body, offset)[0]
    offset += 2
    kinds: list[int] = []
    name = ""
    while offset < end:
        kind, length = struct.unpack_from("!HH", body, offset)
        data = body[offset + 4 : offset + 4 + length]
        offset += 4 + length
        kinds.append(kind)
        if kind == EXT_SNI:
            name = data[5:].decode("ascii")
    return tuple(c for c in ciphers if real(c)), tuple(k for k in kinds if real(k)), name


def ja4(record: bytes) -> str:
    """Отпечаток JA4 приветствия: по нему фильтр узнаёт программу, порядок расширений на него не влияет."""

    def short(text: str) -> str:
        return hashlib.sha256(text.encode("ascii")).hexdigest()[:12]

    ciphers, extensions, _name = parse_hello(record)
    hashed = sorted(f"{kind:04x}" for kind in extensions if kind not in (EXT_SNI, EXT_ALPN))
    signatures = ",".join(f"{value:04x}" for value in _SIGNATURES)
    return (
        f"t13d{len(ciphers):02d}{len(extensions):02d}h2_"
        f"{short(','.join(sorted(f'{value:04x}' for value in ciphers)))}_"
        f"{short(','.join(hashed) + '_' + signatures)}"
    )


def name_offset(record: bytes, host: str) -> int:
    """Где в записи лежит имя сайта. -1 — не нашли."""
    return record.find(host.encode("idna"))


def split_record(record: bytes, at: int) -> tuple[bytes, bytes]:
    """Одно приветствие двумя записями TLS: разрез на байте ``at`` исходной записи.

    Сервер обязан склеить такие записи; фильтр, который читает только первую, имени целиком не увидит.
    """
    body = record[5:]
    cut = max(1, min(len(body) - 1, at - 5))
    head = record[:3]
    return head + _vec16(body[:cut]), head + _vec16(body[cut:])


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return bytes(data)


def send_hello(
    ip: str,
    host: str,
    *,
    port: int = 443,
    timeout: float = HELLO_TIMEOUT_S,
    cancel: SocketCancel | None = None,
    rnd: Random = os.urandom,
) -> HelloResult:
    """Шлёт приветствие Chrome на ``ip`` и сообщает, что пришло в ответ (см. ``send_parts``)."""
    return send_parts(ip, (build_hello(host, rnd=rnd),), port=port, timeout=timeout, cancel=cancel)


def send_parts(
    ip: str,
    parts: tuple[bytes, ...],
    *,
    pause: float = 0.0,
    port: int = 443,
    timeout: float = HELLO_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> HelloResult:
    """Шлёт готовое приветствие кусками ``parts`` (с паузой ``pause`` между ними) и сообщает, что пришло в ответ.

    Каждый кусок уходит отдельным пакетом. ``HELLO_OK`` — сервер ответил своим приветствием; ``HELLO_ALERT`` — сервер
    ответил отказом (приветствие до него всё равно дошло); ``HELLO_RESET`` и
    ``HELLO_TIMEOUT`` — соединение установилось, но ответа на приветствие нет.
    """
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    connected = False
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return HelloResult(HELLO_CANCELLED)
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        connected = True
        # Куски должны уйти отдельными пакетами, а не склеиться в один.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        started = time.perf_counter()
        for index, part in enumerate(parts):
            if index and pause:
                time.sleep(pause)
            sock.sendall(part)
        header = _read_exact(sock, 5)
        took = (time.perf_counter() - started) * 1000.0
        if len(header) < 5:
            # Соединение закрыли, не ответив: для фильтра это то же, что сброс.
            return HelloResult(HELLO_CANCELLED) if token.cancelled else HelloResult(HELLO_RESET, "соединение закрыто без ответа")
        if header[0] == _RECORD_ALERT:
            return HelloResult(HELLO_ALERT, ms=took)
        if header[0] != _RECORD_HANDSHAKE or header[1] != 0x03:
            return HelloResult(HELLO_GARBAGE)
        first = _read_exact(sock, 1)
        if first and first[0] == _HANDSHAKE_SERVER_HELLO:
            return HelloResult(HELLO_OK, ms=took)
        return HelloResult(HELLO_GARBAGE)
    except (socket.timeout, TimeoutError):
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_TIMEOUT if connected else HELLO_CONNECT)
    except OSError as error:
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_RESET if connected else HELLO_CONNECT, str(error))
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)
