"""Сколько байт сервер разрешил прислать ему до своего ответа: окно приёма сервера (Windows).

Окно — сколько данных одна сторона соединения готова принять, пока не подтвердит
полученное. Если окно сервера меньше приветствия браузера (ClientHello с
постквантовым ключом — около 1,7 КБ), браузер отправит приветствие частями, и
winws2 не соберёт его целиком: имя сайта он не узнает, и профиль по списку доменов
не сработает (так 30.10.2026 вёл себя dns.nextdns.io с окном 1448–1460 байт).

Окно читается через ``SIO_TCP_INFO`` (Windows 10 1703+, без прав администратора):
поле ``SndWnd`` — последнее окно, объявленное сервером. На других системах и при
ошибке — None: такой факт просто не известен.
"""

from __future__ import annotations

import ctypes
import socket
import sys

__all__ = ["server_window"]

_SIO_TCP_INFO = 0xD8000027  # _WSAIORW(IOC_VENDOR, 39)


class _TcpInfoV0(ctypes.Structure):
    _fields_ = [
        ("State", ctypes.c_int),
        ("Mss", ctypes.c_ulong),
        ("ConnectionTimeMs", ctypes.c_uint64),
        ("TimestampsEnabled", ctypes.c_ubyte),
        ("RttUs", ctypes.c_ulong),
        ("MinRttUs", ctypes.c_ulong),
        ("BytesInFlight", ctypes.c_ulong),
        ("Cwnd", ctypes.c_ulong),
        ("SndWnd", ctypes.c_ulong),
        ("RcvWnd", ctypes.c_ulong),
        ("RcvBuf", ctypes.c_ulong),
        ("BytesOut", ctypes.c_uint64),
        ("BytesIn", ctypes.c_uint64),
        ("BytesReordered", ctypes.c_ulong),
        ("BytesRetrans", ctypes.c_ulong),
        ("FastRetrans", ctypes.c_ulong),
        ("DupAcksIn", ctypes.c_ulong),
        ("TimeoutEpisodes", ctypes.c_ulong),
        ("SynRetrans", ctypes.c_ubyte),
    ]


def _send_window(sock: socket.socket) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        ws2 = ctypes.WinDLL("ws2_32")
        version = ctypes.c_ulong(0)
        info = _TcpInfoV0()
        returned = ctypes.c_ulong(0)
        status = ws2.WSAIoctl(
            ctypes.c_size_t(sock.fileno()),
            ctypes.c_ulong(_SIO_TCP_INFO),
            ctypes.byref(version),
            ctypes.sizeof(version),
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
            None,
            None,
        )
    except (OSError, AttributeError, ValueError):
        return None
    return int(info.SndWnd) if status == 0 and info.SndWnd else None


def server_window(ip: str, *, port: int = 443, timeout: float = 4.0) -> int | None:
    """Окно приёма сервера сразу после соединения, в байтах. None — не удалось узнать."""
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout) as sock:
            return _send_window(sock)
    except OSError:
        return None
