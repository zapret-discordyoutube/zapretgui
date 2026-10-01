from __future__ import annotations

import ctypes
import socket
import struct
from dataclasses import dataclass
from ctypes import wintypes

from utils.net_resolve import resolve_ipv4


IP_SUCCESS = 0
IP_REQ_TIMED_OUT = 11010


@dataclass(frozen=True, slots=True)
class WindowsPingResult:
    ok: bool
    average_ms: float | None = None
    sent: int = 0
    received: int = 0
    resolved_ip: str | None = None
    error_code: str | None = None
    detail: str = ""
    min_ms: float | None = None
    max_ms: float | None = None
    ttl: int | None = None


class IP_OPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", wintypes.DWORD),
        ("Status", wintypes.DWORD),
        ("RoundTripTime", wintypes.DWORD),
        ("DataSize", wintypes.WORD),
        ("Reserved", wintypes.WORD),
        ("Data", ctypes.c_void_p),
        ("Options", IP_OPTION_INFORMATION),
    ]


class SOCKADDR_IN6(ctypes.Structure):
    _fields_ = [
        ("sin6_family", ctypes.c_short),
        ("sin6_port", ctypes.c_ushort),
        ("sin6_flowinfo", ctypes.c_ulong),
        ("sin6_addr", ctypes.c_ubyte * 16),
        ("sin6_scope_id", ctypes.c_ulong),
    ]


class IPV6_ADDRESS_EX(ctypes.Structure):
    # В заголовках Windows эта структура упакована без выравнивания (26 байт).
    _pack_ = 1
    _fields_ = [
        ("sin6_port", ctypes.c_ushort),
        ("sin6_flowinfo", ctypes.c_ulong),
        ("sin6_addr", ctypes.c_ushort * 8),
        ("sin6_scope_id", ctypes.c_ulong),
    ]


class ICMPV6_ECHO_REPLY(ctypes.Structure):
    _fields_ = [
        ("Address", IPV6_ADDRESS_EX),
        ("Status", ctypes.c_ulong),
        ("RoundTripTime", ctypes.c_uint),
    ]


_AF_INET6_WINDOWS = 23
# Место под служебный блок IO_STATUS_BLOCK, который Windows дописывает в ответ.
_ICMP6_REPLY_EXTRA = 8 + 2 * ctypes.sizeof(ctypes.c_void_p)

_Icmp6CreateFile = None
_Icmp6SendEcho2 = None

if hasattr(ctypes, "windll"):
    try:
        _iphlpapi6 = ctypes.windll.iphlpapi
        _Icmp6CreateFile = _iphlpapi6.Icmp6CreateFile
        _Icmp6CreateFile.argtypes = []
        _Icmp6CreateFile.restype = wintypes.HANDLE

        _Icmp6SendEcho2 = _iphlpapi6.Icmp6SendEcho2
        _Icmp6SendEcho2.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(SOCKADDR_IN6),
            ctypes.POINTER(SOCKADDR_IN6),
            ctypes.c_void_p,
            wintypes.WORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        _Icmp6SendEcho2.restype = wintypes.DWORD
    except Exception:
        _Icmp6CreateFile = None
        _Icmp6SendEcho2 = None

if hasattr(ctypes, "windll"):
    _kernel32 = ctypes.windll.kernel32

    _IcmpCreateFile = None
    _IcmpCloseHandle = None
    _IcmpSendEcho = None

    for _dll_name in ("iphlpapi", "icmp"):
        try:
            _icmp_dll = getattr(ctypes.windll, _dll_name)
            _IcmpCreateFile = _icmp_dll.IcmpCreateFile
            _IcmpCreateFile.argtypes = []
            _IcmpCreateFile.restype = wintypes.HANDLE

            _IcmpCloseHandle = _icmp_dll.IcmpCloseHandle
            _IcmpCloseHandle.argtypes = [wintypes.HANDLE]
            _IcmpCloseHandle.restype = wintypes.BOOL

            _IcmpSendEcho = _icmp_dll.IcmpSendEcho
            _IcmpSendEcho.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.WORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            _IcmpSendEcho.restype = wintypes.DWORD
            break
        except Exception:
            _IcmpCreateFile = None
            _IcmpCloseHandle = None
            _IcmpSendEcho = None

    _GetLastError = _kernel32.GetLastError
    _GetLastError.argtypes = []
    _GetLastError.restype = wintypes.DWORD
else:  # pragma: no cover
    _IcmpCreateFile = None
    _IcmpCloseHandle = None
    _IcmpSendEcho = None
    _GetLastError = None


def _is_windows_icmp_available() -> bool:
    return all(
        fn is not None
        for fn in (_IcmpCreateFile, _IcmpCloseHandle, _IcmpSendEcho, _GetLastError)
    )


def _resolve_ipv4(host: str, timeout: float) -> tuple[str | None, str | None]:
    """Разрешает имя в IPv4 с дедлайном.

    ``socket.gethostbyname`` прерывать нельзя и таймаута у него нет: на мёртвом
    DNS он висел десятки секунд и удерживал поток пула, из-за чего приложение
    не закрывалось.
    """
    try:
        ip = resolve_ipv4(str(host or "").strip(), timeout=timeout)
    except Exception:
        return None, "RESOLVE_ERR"
    if not ip:
        return None, "DNS_ERR"
    return ip, None


def _ipv4_to_dword(ip: str) -> int:
    """IPAddr для IcmpSendEcho: байты адреса в памяти идут в сетевом порядке.

    Поэтому число собирается в порядке байтов процессора (little-endian), а не
    как big-endian: иначе пинговался адрес задом наперёд (142.251.150.4 →
    4.150.251.142), и проверка честно писала «таймаут» у живого сервера.
    """
    return int(struct.unpack("<I", socket.inet_aton(ip))[0])


def _summarize(
    rtts: list[float], sent: int, ip: str, last_status: int | None, ttl: int | None = None
) -> WindowsPingResult:
    received = len(rtts)
    if received > 0:
        average_ms = sum(rtts) / received
        return WindowsPingResult(
            ok=True,
            average_ms=average_ms,
            sent=sent,
            received=received,
            resolved_ip=ip,
            detail=f"{average_ms:.0f}ms",
            min_ms=min(rtts),
            max_ms=max(rtts),
            ttl=ttl,
        )
    if last_status == IP_REQ_TIMED_OUT:
        return WindowsPingResult(ok=False, sent=sent, received=0, resolved_ip=ip, error_code="TIMEOUT", detail="Timeout")
    return WindowsPingResult(
        ok=False,
        sent=sent,
        received=0,
        resolved_ip=ip,
        error_code=f"ICMP_{int(last_status or 0)}",
        detail=f"ICMP status {int(last_status or 0)}",
    )


def ping_ipv6_winapi(ip: str, *, count: int, timeout_ms: int, cancelled=None) -> WindowsPingResult:
    """Пингует IPv6-адрес через Windows ICMP API (Icmp6SendEcho2) без ping.exe."""
    sent = max(0, int(count))
    if _Icmp6CreateFile is None or _Icmp6SendEcho2 is None or _IcmpCloseHandle is None or _GetLastError is None:
        return WindowsPingResult(
            ok=False, sent=sent, received=0, error_code="UNSUPPORTED", detail="Windows ICMPv6 API unavailable"
        )
    try:
        packed = socket.inet_pton(socket.AF_INET6, ip.split("%", 1)[0])
    except OSError:
        return WindowsPingResult(ok=False, sent=sent, received=0, error_code="RESOLVE_ERR", detail="Bad IPv6 address")

    handle = _Icmp6CreateFile()
    if not handle or handle == ctypes.c_void_p(-1).value:
        return WindowsPingResult(
            ok=False, sent=sent, received=0, resolved_ip=ip, error_code="ICMP_OPEN_FAILED", detail="Icmp6CreateFile failed"
        )

    source = SOCKADDR_IN6()
    source.sin6_family = _AF_INET6_WINDOWS
    destination = SOCKADDR_IN6()
    destination.sin6_family = _AF_INET6_WINDOWS
    ctypes.memmove(destination.sin6_addr, packed, 16)

    request_data = b"zapret"
    reply_size = ctypes.sizeof(ICMPV6_ECHO_REPLY) + len(request_data) + 8 + _ICMP6_REPLY_EXTRA
    rtts: list[float] = []
    last_status: int | None = None
    try:
        for attempt in range(sent):
            if cancelled is not None and cancelled():
                sent = attempt
                break
            request_buffer = ctypes.create_string_buffer(request_data)
            reply_buffer = ctypes.create_string_buffer(reply_size)
            result = _Icmp6SendEcho2(
                handle,
                None,
                None,
                None,
                ctypes.byref(source),
                ctypes.byref(destination),
                request_buffer,
                len(request_data),
                None,
                reply_buffer,
                reply_size,
                max(1, int(timeout_ms)),
            )
            if result:
                reply = ICMPV6_ECHO_REPLY.from_buffer(reply_buffer)
                last_status = int(reply.Status)
                if last_status == IP_SUCCESS:
                    rtts.append(float(reply.RoundTripTime))
            else:
                last_status = int(_GetLastError())
    finally:
        _IcmpCloseHandle(handle)
    return _summarize(rtts, sent, ip, last_status)


def ping_ipv4_host_winapi(
    host: str,
    *,
    count: int,
    timeout_ms: int,
    resolved_ip: str | None = None,
    cancelled=None,
) -> WindowsPingResult:
    """Пингует IPv4-хост через Windows ICMP API без вызова ping.exe.

    ``resolved_ip`` позволяет вызывающему передать уже известный адрес и не
    платить за повторное разрешение имени. ``cancelled`` — необязательная
    функция «пора остановиться»: проверяется перед каждым пакетом.
    """
    sent = max(0, int(count))

    if not _is_windows_icmp_available():
        return WindowsPingResult(
            ok=False,
            sent=sent,
            received=0,
            error_code="UNSUPPORTED",
            detail="Windows ICMP API unavailable",
        )

    if resolved_ip:
        resolve_error = None
    else:
        resolved_ip, resolve_error = _resolve_ipv4(
            host, timeout=max(1.0, float(timeout_ms) / 1000.0),
        )
    if not resolved_ip:
        return WindowsPingResult(
            ok=False,
            sent=sent,
            received=0,
            error_code=resolve_error or "DNS_ERR",
            detail="DNS resolve failed",
        )

    handle = _IcmpCreateFile()
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        return WindowsPingResult(
            ok=False,
            sent=sent,
            received=0,
            resolved_ip=resolved_ip,
            error_code="ICMP_OPEN_FAILED",
            detail="IcmpCreateFile failed",
        )

    request_data = b"zapret"
    reply_size = ctypes.sizeof(ICMP_ECHO_REPLY) + len(request_data) + 8
    rtts: list[float] = []
    last_status: int | None = None
    ttl: int | None = None

    try:
        destination_ip = _ipv4_to_dword(resolved_ip)
        for attempt in range(sent):
            if cancelled is not None and cancelled():
                sent = attempt
                break
            request_buffer = ctypes.create_string_buffer(request_data)
            reply_buffer = ctypes.create_string_buffer(reply_size)
            result = _IcmpSendEcho(
                handle,
                destination_ip,
                request_buffer,
                len(request_data),
                None,
                reply_buffer,
                reply_size,
                max(1, int(timeout_ms)),
            )
            if result:
                reply = ICMP_ECHO_REPLY.from_buffer(reply_buffer)
                last_status = int(reply.Status)
                if last_status == IP_SUCCESS:
                    rtts.append(float(reply.RoundTripTime))
                    ttl = int(reply.Options.Ttl)
            else:
                last_status = int(_GetLastError())
    finally:
        _IcmpCloseHandle(handle)

    return _summarize(rtts, sent, resolved_ip, last_status, ttl)
