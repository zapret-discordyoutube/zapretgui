"""WebSocket-клиент для маршрутов Telegram: релей kwsN, фронты Cloudflare, туннель.

Upgrade написан вручную, потому что фронтам Cloudflare нужен повтор запроса на
том же TLS-соединении: примерно 40% фронтов сначала отвечают 503/429 без тела,
а повтор через ~25 мс проходит (ZaStoGram, WssSocket.cpp). Обычные библиотеки
так не умеют.

Подключение разбито на этапы (TCP, TLS, upgrade), чтобы учёт здоровья маршрутов
отличал «адрес недоступен» от «сервер ответил не так».
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import socket as _socket
import ssl
import struct
import time
from dataclasses import dataclass


WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TCP_CONNECT_TIMEOUT = 2.5
UPGRADE_TIMEOUT = 8.0
BUSY_RETRIES = 8
BUSY_RETRY_DELAY = 0.025
MAX_FRAME = 2 * 1024 * 1024
MAX_HEADER = 32 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ORIGIN = "https://web.telegram.org"

STAGE_TCP = "tcp"
STAGE_TLS = "tls"
STAGE_UPGRADE = "upgrade"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

_SSL_CONTEXT: ssl.SSLContext | None = None


def default_ssl_context() -> ssl.SSLContext:
    """Системное хранилище сертификатов: имя сервера проверяется по-настоящему."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


class WsConnectError(Exception):
    """Не удалось открыть WebSocket. stage — на каком этапе."""

    def __init__(self, stage: str, message: str, *, status_code: int = 0):
        self.stage = stage
        self.status_code = int(status_code or 0)
        super().__init__(message)

    @property
    def tcp_reached(self) -> bool:
        return self.stage != STAGE_TCP


class WsProtocolError(Exception):
    """Сервер нарушил протокол WebSocket после upgrade."""


@dataclass(frozen=True, slots=True)
class WsTarget:
    """Куда подключаться: адрес для TCP, имя для TLS/Host и путь."""

    connect_host: str
    sni: str
    path: str = "/apiws"
    port: int = 443


def apply_socket_options(transport, buffer_size: int = 256 * 1024) -> None:
    sock = transport.get_extra_info("socket") if transport is not None else None
    if sock is None:
        return
    try:
        sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass
    try:
        size = max(4 * 1024, int(buffer_size))
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, size)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF, size)
    except (OSError, AttributeError, TypeError, ValueError):
        pass


def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return data
    size = len(data)
    repeated = (key * (size // 4 + 1))[:size]
    return (int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")).to_bytes(size, "big")


def encode_frame(opcode: int, payload: bytes) -> bytes:
    """Кадр от клиента: FIN, маска обязательна."""
    size = len(payload)
    head = bytes((0x80 | (opcode & 0x0F),))
    if size < 126:
        head += bytes((0x80 | size,))
    elif size < 65536:
        head += bytes((0x80 | 126,)) + struct.pack(">H", size)
    else:
        head += bytes((0x80 | 127,)) + struct.pack(">Q", size)
    key = os.urandom(4)
    return head + key + _mask(payload, key)


def _accept_value(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + WS_GUID).digest()).decode("ascii")


def _build_request(target: WsTarget, key: str) -> bytes:
    host = target.sni if target.port == 443 else f"{target.sni}:{target.port}"
    return (
        f"GET {target.path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: binary\r\n"
        f"Origin: {ORIGIN}\r\n"
        f"User-Agent: {USER_AGENT}\r\n"
        "\r\n"
    ).encode("ascii")


async def _read_response_head(reader: asyncio.StreamReader) -> tuple[int, str, dict[str, str]]:
    raw = await reader.readuntil(b"\r\n\r\n")
    if len(raw) > MAX_HEADER:
        raise WsConnectError(STAGE_UPGRADE, "слишком длинный ответ upgrade")
    lines = raw.decode("latin-1").split("\r\n")
    status_line = lines[0]
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1."):
        raise WsConnectError(STAGE_UPGRADE, f"не HTTP-ответ: {status_line[:80]!r}")
    try:
        status = int(parts[1])
    except ValueError as exc:
        raise WsConnectError(STAGE_UPGRADE, f"не HTTP-ответ: {status_line[:80]!r}") from exc
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return status, status_line, headers


def _is_busy_retryable(status: int, headers: dict[str, str]) -> bool:
    """Фронт «занят»: 503/429 без тела и без закрытия соединения."""
    if status not in (429, 503):
        return False
    if headers.get("connection", "").lower() == "close":
        return False
    return headers.get("content-length", "") == "0"


class WebSocket:
    """Открытый WebSocket поверх asyncio-потоков."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: WsTarget):
        self.reader = reader
        self.writer = writer
        self.target = target
        self.opened_at = time.monotonic()
        self._closed = False
        self._fragments = bytearray()

    @property
    def is_closing(self) -> bool:
        return self._closed or self.writer.is_closing() or self.reader.at_eof()

    def has_unread_data(self) -> bool:
        """Сервер что-то прислал без запроса: такой запасной сокет брать нельзя."""
        buffer = getattr(self.reader, "_buffer", None)
        return bool(buffer) or self.reader.at_eof()

    async def send(self, payload: bytes) -> None:
        if self._closed:
            raise ConnectionError("WebSocket закрыт")
        self.writer.write(encode_frame(OP_BINARY, payload))
        await self.writer.drain()

    async def send_many(self, payloads: list[bytes]) -> None:
        """Несколько кадров одной записью: каждый пакет — отдельный кадр."""
        if self._closed:
            raise ConnectionError("WebSocket закрыт")
        if not payloads:
            return
        self.writer.write(b"".join(encode_frame(OP_BINARY, item) for item in payloads))
        await self.writer.drain()

    async def recv(self) -> bytes | None:
        """Следующее двоичное сообщение. None — сервер закрыл соединение."""
        while not self._closed:
            try:
                head = await self.reader.readexactly(2)
            except asyncio.IncompleteReadError:
                self._closed = True
                return None
            fin = bool(head[0] & 0x80)
            opcode = head[0] & 0x0F
            if head[1] & 0x80:
                raise WsProtocolError("сервер прислал замаскированный кадр")
            size = head[1] & 0x7F
            if size == 126:
                size = struct.unpack(">H", await self.reader.readexactly(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", await self.reader.readexactly(8))[0]
            if size > MAX_FRAME:
                raise WsProtocolError(f"слишком большой кадр: {size}")
            payload = await self.reader.readexactly(size) if size else b""

            if opcode == OP_CLOSE:
                self._closed = True
                return None
            if opcode == OP_PING:
                self.writer.write(encode_frame(OP_PONG, payload))
                await self.writer.drain()
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_TEXT:
                raise WsProtocolError("сервер прислал текстовый кадр")
            if opcode not in (OP_BINARY, OP_CONT):
                raise WsProtocolError(f"неизвестный opcode {opcode}")
            if not fin:
                self._fragments.extend(payload)
                continue
            if self._fragments:
                self._fragments.extend(payload)
                payload = bytes(self._fragments)
                self._fragments.clear()
            return payload
        return None

    async def close(self) -> None:
        # Как ZaStoGram: без кадра close, просто закрываем TCP.
        self._closed = True
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), timeout=1.0)
        except BaseException:
            pass


async def connect(
    target: WsTarget,
    *,
    tcp_timeout: float = TCP_CONNECT_TIMEOUT,
    upgrade_timeout: float = UPGRADE_TIMEOUT,
    busy_retries: int = BUSY_RETRIES,
    ssl_context: ssl.SSLContext | None = None,
    buffer_size: int = 256 * 1024,
) -> WebSocket:
    """TCP → TLS (SNI = target.sni) → upgrade. Ошибки — WsConnectError с этапом."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target.connect_host, target.port),
            timeout=tcp_timeout,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise WsConnectError(STAGE_TCP, f"{type(exc).__name__}: {exc}".rstrip(": ")) from exc

    apply_socket_options(writer.transport, buffer_size)
    stage = STAGE_TLS
    try:
        async with asyncio.timeout(upgrade_timeout):
            await writer.start_tls(
                ssl_context or default_ssl_context(),
                server_hostname=target.sni,
            )
            stage = STAGE_UPGRADE
            for attempt in range(max(1, int(busy_retries))):
                key = base64.b64encode(os.urandom(16)).decode("ascii")
                writer.write(_build_request(target, key))
                await writer.drain()
                status, status_line, headers = await _read_response_head(reader)
                if status == 101:
                    if headers.get("sec-websocket-accept", "") != _accept_value(key):
                        raise WsConnectError(STAGE_UPGRADE, "неверный Sec-WebSocket-Accept", status_code=101)
                    return WebSocket(reader, writer, target)
                if _is_busy_retryable(status, headers) and attempt + 1 < busy_retries:
                    await asyncio.sleep(BUSY_RETRY_DELAY)
                    continue
                raise WsConnectError(STAGE_UPGRADE, f"HTTP {status_line}", status_code=status)
    except asyncio.CancelledError:
        writer.close()
        raise
    except WsConnectError:
        writer.close()
        raise
    except Exception as exc:
        writer.close()
        raise WsConnectError(stage, f"{type(exc).__name__}: {exc}".rstrip(": ")) from exc
    writer.close()
    raise WsConnectError(STAGE_UPGRADE, "upgrade не удался")


__all__ = [
    "STAGE_TCP",
    "STAGE_TLS",
    "STAGE_UPGRADE",
    "WebSocket",
    "WsConnectError",
    "WsProtocolError",
    "WsTarget",
    "apply_socket_options",
    "connect",
    "default_ssl_context",
    "encode_frame",
]
