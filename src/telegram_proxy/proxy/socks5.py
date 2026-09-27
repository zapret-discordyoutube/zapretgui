# telegram_proxy/socks5.py
"""SOCKS5 (RFC 1928) ровно в том объёме, что нужен Telegram.

Сервер (вход прокси): без пароля, CONNECT и UDP ASSOCIATE, адреса IPv4/IPv6/домен.
Клиент (внешний SOCKS5): CONNECT и UDP ASSOCIATE, вход без пароля или по
логину и паролю (RFC 1929), по желанию поверх TLS.
"""

import asyncio
import inspect
import struct
import logging
import ssl
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Callable, Optional

log = logging.getLogger("tg_proxy.socks5")

# SOCKS5 constants
SOCKS_VER = 0x05
AUTH_NONE = 0x00
AUTH_USERPASS = 0x02
CMD_CONNECT = 0x01
CMD_UDP_ASSOCIATE = 0x03
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04
REP_SUCCESS = 0x00
REP_GENERAL_FAILURE = 0x01
REP_CONNECTION_NOT_ALLOWED = 0x02
REP_CONN_REFUSED = 0x05
REP_CMD_NOT_SUPPORTED = 0x07
REP_ATYP_NOT_SUPPORTED = 0x08


class Socks5Error(Exception):
    """SOCKS5 protocol error."""


class Socks5ReplyError(Socks5Error):
    """A valid SOCKS5 server rejected the requested command or target."""

    def __init__(self, command_name: str, reply_code: int) -> None:
        self.command_name = str(command_name)
        self.reply_code = int(reply_code)
        super().__init__(
            f"Upstream proxy {self.command_name} failed (REP=0x{self.reply_code:02X})"
        )


@dataclass(frozen=True, slots=True)
class UdpAssociateRequest:
    client_host: str
    client_port: int


@dataclass(frozen=True, slots=True)
class UdpAssociateSession:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    relay_host: str
    relay_port: int


@dataclass(frozen=True, slots=True)
class UdpPacket:
    target_host: str
    target_port: int
    payload: bytes


async def handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    allow_udp_associate: bool = False,
    on_udp_associate: Optional[Callable[[], object]] = None,
) -> Optional[tuple[str, int] | UdpAssociateRequest]:
    """Perform SOCKS5 handshake. Returns CONNECT target, UDP request, or None."""
    try:
        return await _do_handshake(
            reader,
            writer,
            allow_udp_associate=allow_udp_associate,
            on_udp_associate=on_udp_associate,
        )
    except (
        Socks5Error,
        asyncio.IncompleteReadError,
        asyncio.TimeoutError,
        ConnectionError,
        struct.error,
    ) as e:
        log.debug("SOCKS5 handshake failed: %s", e)
        return None
    except Exception:
        log.exception("Unexpected SOCKS5 error")
        return None


async def _do_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    allow_udp_associate: bool = False,
    on_udp_associate: Optional[Callable[[], object]] = None,
) -> Optional[tuple[str, int] | UdpAssociateRequest]:
    # --- Phase 1: Greeting ---
    header = await asyncio.wait_for(reader.readexactly(2), timeout=10.0)
    ver, nmethods = struct.unpack("!BB", header)
    if ver != SOCKS_VER:
        raise Socks5Error(f"Bad SOCKS version: {ver}")

    methods = await reader.readexactly(nmethods)
    if AUTH_NONE not in methods:
        # No acceptable auth method
        writer.write(struct.pack("!BB", SOCKS_VER, 0xFF))
        await writer.drain()
        raise Socks5Error("Client doesn't support no-auth")

    # Accept no-auth
    writer.write(struct.pack("!BB", SOCKS_VER, AUTH_NONE))
    await writer.drain()

    # --- Phase 2: Request ---
    req_header = await asyncio.wait_for(reader.readexactly(4), timeout=10.0)
    ver, cmd, _rsv, atyp = struct.unpack("!BBBB", req_header)

    if ver != SOCKS_VER:
        raise Socks5Error(f"Bad version in request: {ver}")

    target_host, target_port = await _read_address(reader, atyp)

    if cmd == CMD_UDP_ASSOCIATE and allow_udp_associate:
        try:
            bind_result = on_udp_associate() if on_udp_associate else ("0.0.0.0", 0)
            if inspect.isawaitable(bind_result):
                bind_result = await bind_result
            bind_host, bind_port = bind_result
        except Exception:
            _send_reply(writer, REP_GENERAL_FAILURE)
            await writer.drain()
            raise
        _send_reply(writer, REP_SUCCESS, bind_host=bind_host, bind_port=bind_port)
        await writer.drain()
        return UdpAssociateRequest(target_host, target_port)

    if cmd != CMD_CONNECT:
        _send_reply(writer, REP_CMD_NOT_SUPPORTED)
        await writer.drain()
        raise Socks5Error(f"Unsupported command: {cmd}")

    # Send success reply (bound address 0.0.0.0:0)
    _send_reply(writer, REP_SUCCESS)
    await writer.drain()

    return (target_host, target_port)


async def _read_address(reader: asyncio.StreamReader, atyp: int) -> tuple[str, int]:
    if atyp == ATYP_IPV4:
        raw_addr = await reader.readexactly(4)
        target_host = ".".join(str(b) for b in raw_addr)
    elif atyp == ATYP_DOMAIN:
        domain_len = (await reader.readexactly(1))[0]
        domain = await reader.readexactly(domain_len)
        target_host = domain.decode("ascii", errors="replace")
    elif atyp == ATYP_IPV6:
        raw_addr = await reader.readexactly(16)
        target_host = str(IPv6Address(raw_addr))
    else:
        raise Socks5Error(f"Unknown ATYP: {atyp}")
    raw_port = await reader.readexactly(2)
    return target_host, struct.unpack("!H", raw_port)[0]


def _encode_address(host: str) -> tuple[int, bytes]:
    try:
        target_addr = ip_address(str(host))
    except ValueError:
        target_addr = None

    if isinstance(target_addr, IPv4Address):
        return ATYP_IPV4, target_addr.packed
    if isinstance(target_addr, IPv6Address):
        return ATYP_IPV6, target_addr.packed

    domain_bytes = str(host or "").encode("ascii")
    if len(domain_bytes) > 255:
        raise Socks5Error("Domain name is too long for SOCKS5")
    return ATYP_DOMAIN, struct.pack("!B", len(domain_bytes)) + domain_bytes


def _send_reply(
    writer: asyncio.StreamWriter,
    rep: int,
    *,
    bind_host: str = "0.0.0.0",
    bind_port: int = 0,
) -> None:
    """Send SOCKS5 reply with bound address."""
    atyp, encoded_host = _encode_address(bind_host)
    writer.write(
        struct.pack("!BBBB", SOCKS_VER, rep, 0x00, atyp)
        + encoded_host
        + struct.pack("!H", int(bind_port or 0))
    )


def parse_udp_packet(data: bytes) -> UdpPacket:
    if len(data) < 4:
        raise Socks5Error("UDP packet is too short")
    rsv, frag, atyp = struct.unpack("!HBB", data[:4])
    if rsv != 0:
        raise Socks5Error("Bad UDP reserved field")
    if frag != 0:
        raise Socks5Error("Fragmented SOCKS5 UDP packets are not supported")

    offset = 4
    if atyp == ATYP_IPV4:
        if len(data) < offset + 4 + 2:
            raise Socks5Error("UDP IPv4 packet is too short")
        target_host = str(IPv4Address(data[offset:offset + 4]))
        offset += 4
    elif atyp == ATYP_IPV6:
        if len(data) < offset + 16 + 2:
            raise Socks5Error("UDP IPv6 packet is too short")
        target_host = str(IPv6Address(data[offset:offset + 16]))
        offset += 16
    elif atyp == ATYP_DOMAIN:
        if len(data) < offset + 1:
            raise Socks5Error("UDP domain packet is too short")
        domain_len = data[offset]
        offset += 1
        if len(data) < offset + domain_len + 2:
            raise Socks5Error("UDP domain packet is too short")
        target_host = data[offset:offset + domain_len].decode("ascii", errors="replace")
        offset += domain_len
    else:
        raise Socks5Error(f"Unknown UDP ATYP: {atyp}")

    target_port = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    return UdpPacket(target_host, target_port, data[offset:])


def build_udp_packet(target_host: str, target_port: int, payload: bytes) -> bytes:
    atyp, encoded_host = _encode_address(target_host)
    return (
        struct.pack("!HBB", 0, 0, atyp)
        + encoded_host
        + struct.pack("!H", int(target_port or 0))
        + bytes(payload or b"")
    )


async def _open_socks5_control_connection(
    proxy_host: str,
    proxy_port: int,
    *,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
    tls: bool = False,
    tls_server_name: str = "",
    tls_verify: bool = False,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    ssl_context = None
    server_hostname = None
    if tls:
        ssl_context = ssl.create_default_context()
        if not tls_verify:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        server_hostname = str(tls_server_name or proxy_host or "").strip() or None

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            proxy_host,
            proxy_port,
            ssl=ssl_context,
            server_hostname=server_hostname,
        ),
        timeout=timeout,
    )

    has_creds = bool(username)
    if has_creds:
        writer.write(struct.pack("!BBBB", SOCKS_VER, 2, AUTH_NONE, AUTH_USERPASS))
    else:
        writer.write(struct.pack("!BBB", SOCKS_VER, 1, AUTH_NONE))
    await writer.drain()

    reply = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
    ver, method = struct.unpack("!BB", reply)
    if ver != SOCKS_VER:
        raise Socks5Error(f"Upstream proxy bad SOCKS version: {ver}")

    if method == AUTH_USERPASS:
        if not has_creds:
            raise Socks5Error("Upstream proxy requires auth but no credentials provided")
        uname = username.encode("utf-8")
        passwd = password.encode("utf-8")
        auth_req = struct.pack("!BB", 0x01, len(uname)) + uname
        auth_req += struct.pack("!B", len(passwd)) + passwd
        writer.write(auth_req)
        await writer.drain()
        auth_reply = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        _auth_ver, auth_status = struct.unpack("!BB", auth_reply)
        if auth_status != 0x00:
            raise Socks5Error(f"Upstream proxy auth failed (status=0x{auth_status:02X})")
    elif method == AUTH_NONE:
        pass
    elif method == 0xFF:
        raise Socks5Error("Upstream proxy rejected all auth methods")
    else:
        raise Socks5Error(f"Upstream proxy selected unsupported method 0x{method:02X}")

    return reader, writer


async def _read_socks5_reply_address(
    reader: asyncio.StreamReader,
    *,
    timeout: float,
    command_name: str,
) -> tuple[str, int]:
    resp = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
    ver, rep, _rsv, atyp = struct.unpack("!BBBB", resp)

    if ver != SOCKS_VER:
        raise Socks5Error(f"Upstream proxy bad reply version: {ver}")
    if rep != REP_SUCCESS:
        raise Socks5ReplyError(command_name, rep)

    return await _read_address(reader, atyp)


async def open_udp_associate_via_socks5(
    proxy_host: str,
    proxy_port: int,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
    tls: bool = False,
    tls_server_name: str = "",
    tls_verify: bool = False,
) -> UdpAssociateSession:
    """Open UDP ASSOCIATE through an upstream SOCKS5 proxy."""
    reader, writer = await _open_socks5_control_connection(
        proxy_host,
        proxy_port,
        username=username,
        password=password,
        timeout=timeout,
        tls=tls,
        tls_server_name=tls_server_name,
        tls_verify=tls_verify,
    )

    try:
        writer.write(
            struct.pack("!BBBB", SOCKS_VER, CMD_UDP_ASSOCIATE, 0x00, ATYP_IPV4)
            + IPv4Address("0.0.0.0").packed
            + struct.pack("!H", 0)
        )
        await writer.drain()
        relay_host, relay_port = await _read_socks5_reply_address(
            reader,
            timeout=timeout,
            command_name="UDP ASSOCIATE",
        )
        if relay_host == "0.0.0.0":
            relay_host = str(proxy_host)
        return UdpAssociateSession(reader, writer, relay_host, relay_port)
    except Exception:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        raise


# ---- SOCKS5 client (outbound connect through upstream proxy) ----


async def connect_via_socks5(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    username: str = "",
    password: str = "",
    timeout: float = 10.0,
    tls: bool = False,
    tls_server_name: str = "",
    tls_verify: bool = False,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Подключиться к target через внешний SOCKS5. Ошибки протокола — Socks5Error."""
    reader, writer = await _open_socks5_control_connection(
        proxy_host,
        proxy_port,
        username=username,
        password=password,
        timeout=timeout,
        tls=tls,
        tls_server_name=tls_server_name,
        tls_verify=tls_verify,
    )
    try:
        atyp, encoded_host = _encode_address(target_host)
        writer.write(
            struct.pack("!BBBB", SOCKS_VER, CMD_CONNECT, 0x00, atyp)
            + encoded_host
            + struct.pack("!H", int(target_port))
        )
        await writer.drain()
        await _read_socks5_reply_address(reader, timeout=timeout, command_name="CONNECT")
        return reader, writer
    except Exception:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        raise
