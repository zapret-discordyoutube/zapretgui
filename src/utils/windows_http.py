"""HTTPS-запросы через WinHTTP: проверка сертификата силами Windows, без curl.

Зачем свой модуль
-----------------
Диагностике нужно не просто «открылось / не открылось», а причина:

* чужой сертификат — улика подмены адреса или перехвата трафика;
* обрыв на установке шифрования — типичный почерк DPI;
* отказ TCP — адрес недоступен вовсе.

WinHTTP проверяет цепочку сертификатов тем же хранилищем и тем же CryptoAPI,
что и остальная Windows (включая догрузку промежуточных сертификатов), а
флаги ``SECURE_FAILURE`` позволяют отличить плохой сертификат от оборванного
соединения. Проверка отзыва сертификатов намеренно выключена: списки отзыва
часто недоступны из России, и это давало бы ложные «плохие сертификаты».

Отмена: ``HttpCancel.cancel()`` закрывает дескрипторы из другого потока,
после чего блокирующий вызов WinHTTP сразу возвращается.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass

__all__ = [
    "HttpCancel",
    "HttpsResult",
    "TLS_1_2",
    "TLS_1_3",
    "https_request",
    "is_tls13_supported",
    "is_winhttp_available",
]


TLS_1_2 = 0x00000800
TLS_1_3 = 0x00002000

WINHTTP_ACCESS_TYPE_NO_PROXY = 1
WINHTTP_FLAG_SECURE = 0x00800000
WINHTTP_OPTION_SECURE_PROTOCOLS = 84
WINHTTP_OPTION_DISABLE_FEATURE = 63
WINHTTP_DISABLE_REDIRECTS = 0x00000002
WINHTTP_OPTION_CONNECTION_INFO = 93
WINHTTP_QUERY_STATUS_CODE = 19
WINHTTP_QUERY_FLAG_NUMBER = 0x20000000
WINHTTP_CALLBACK_STATUS_SECURE_FAILURE = 0x00010000
WINHTTP_CALLBACK_FLAG_SECURE_FAILURE = 0x00010000

# Флаги из информации SECURE_FAILURE.
CERT_FLAG_REV_FAILED = 0x00000001
CERT_FLAG_INVALID_CERT = 0x00000002
CERT_FLAG_REVOKED = 0x00000004
CERT_FLAG_INVALID_CA = 0x00000008
CERT_FLAG_CN_INVALID = 0x00000010
CERT_FLAG_DATE_INVALID = 0x00000020
CERT_FLAG_WRONG_USAGE = 0x00000040
SECURITY_CHANNEL_ERROR = 0x80000000
_CERT_PROBLEM_FLAGS = (
    CERT_FLAG_INVALID_CERT
    | CERT_FLAG_REVOKED
    | CERT_FLAG_INVALID_CA
    | CERT_FLAG_CN_INVALID
    | CERT_FLAG_DATE_INVALID
    | CERT_FLAG_WRONG_USAGE
)

ERROR_WINHTTP_TIMEOUT = 12002
ERROR_WINHTTP_NAME_NOT_RESOLVED = 12007
ERROR_WINHTTP_OPERATION_CANCELLED = 12017
ERROR_WINHTTP_CANNOT_CONNECT = 12029
ERROR_WINHTTP_CONNECTION_ERROR = 12030
ERROR_WINHTTP_RESEND_REQUEST = 12032
ERROR_WINHTTP_SECURE_CERT_DATE_INVALID = 12037
ERROR_WINHTTP_SECURE_CERT_CN_INVALID = 12038
ERROR_WINHTTP_SECURE_INVALID_CA = 12045
ERROR_WINHTTP_SECURE_CERT_REV_FAILED = 12057
ERROR_WINHTTP_INVALID_SERVER_RESPONSE = 12152
ERROR_WINHTTP_SECURE_CHANNEL_ERROR = 12157
ERROR_WINHTTP_SECURE_INVALID_CERT = 12169
ERROR_WINHTTP_SECURE_CERT_REVOKED = 12170
ERROR_WINHTTP_SECURE_FAILURE = 12175
ERROR_WINHTTP_SECURE_CERT_WRONG_USAGE = 12179
ERROR_INVALID_HANDLE = 6

_CERT_ERRORS = frozenset(
    {
        ERROR_WINHTTP_SECURE_CERT_DATE_INVALID,
        ERROR_WINHTTP_SECURE_CERT_CN_INVALID,
        ERROR_WINHTTP_SECURE_INVALID_CA,
        ERROR_WINHTTP_SECURE_INVALID_CERT,
        ERROR_WINHTTP_SECURE_CERT_REVOKED,
        ERROR_WINHTTP_SECURE_CERT_WRONG_USAGE,
    }
)

# Виды исхода запроса. Диагностика рассуждает только ими.
KIND_OK = "ok"
KIND_CERT = "cert"
KIND_TLS = "tls"
KIND_RESET = "reset"
KIND_TIMEOUT = "timeout"
KIND_CONNECT = "connect"
KIND_DNS = "dns"
KIND_CANCELLED = "cancelled"
KIND_UNSUPPORTED = "unsupported"
KIND_ERROR = "error"


@dataclass(frozen=True, slots=True)
class HttpsResult:
    status: int | None = None
    error: int = 0
    cert_flags: int = 0
    remote_ip: str = ""
    body: bytes = b""
    read_error: int = 0
    elapsed_ms: float = 0.0

    @property
    def kind(self) -> str:
        if self.status is not None:
            return KIND_OK
        error = int(self.error)
        if error in (ERROR_WINHTTP_OPERATION_CANCELLED, ERROR_INVALID_HANDLE):
            return KIND_CANCELLED
        if error == -1:
            return KIND_UNSUPPORTED
        if error in _CERT_ERRORS or self.cert_flags & _CERT_PROBLEM_FLAGS:
            return KIND_CERT
        if error in (ERROR_WINHTTP_SECURE_FAILURE, ERROR_WINHTTP_SECURE_CHANNEL_ERROR):
            return KIND_TLS
        if error in (ERROR_WINHTTP_CONNECTION_ERROR, ERROR_WINHTTP_INVALID_SERVER_RESPONSE, ERROR_WINHTTP_RESEND_REQUEST):
            return KIND_RESET
        if error == ERROR_WINHTTP_TIMEOUT:
            return KIND_TIMEOUT
        if error == ERROR_WINHTTP_CANNOT_CONNECT:
            return KIND_CONNECT
        if error == ERROR_WINHTTP_NAME_NOT_RESOLVED:
            return KIND_DNS
        return KIND_ERROR

    @property
    def cert_problem(self) -> str:
        """Человеческое описание проблемы сертификата (пусто, если её нет)."""
        flags = int(self.cert_flags)
        error = int(self.error)
        if flags & CERT_FLAG_CN_INVALID or error == ERROR_WINHTTP_SECURE_CERT_CN_INVALID:
            return "сертификат выдан другому сайту"
        if flags & CERT_FLAG_INVALID_CA or error == ERROR_WINHTTP_SECURE_INVALID_CA:
            return "сертификат выдан неизвестным центром"
        if flags & CERT_FLAG_DATE_INVALID or error == ERROR_WINHTTP_SECURE_CERT_DATE_INVALID:
            return "сертификат просрочен или ещё не действует (проверьте часы Windows)"
        if flags & CERT_FLAG_REVOKED or error == ERROR_WINHTTP_SECURE_CERT_REVOKED:
            return "сертификат отозван"
        if flags & CERT_FLAG_WRONG_USAGE or error == ERROR_WINHTTP_SECURE_CERT_WRONG_USAGE:
            return "сертификат не предназначен для сайтов"
        if flags & CERT_FLAG_INVALID_CERT or error == ERROR_WINHTTP_SECURE_INVALID_CERT:
            return "сертификат повреждён"
        return ""


class HttpCancel:
    """Общая кнопка «Стоп» для пачки запросов из разных потоков."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handles: set[int] = set()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            handles = list(self._handles)
            self._handles.clear()
        for handle in handles:
            _close(handle)

    def _track(self, handle: int) -> bool:
        with self._lock:
            if self._cancelled:
                return False
            self._handles.add(handle)
            return True

    def _release(self, handle: int) -> bool:
        """True, если дескриптор ещё наш и закрыть его должен вызывающий."""
        with self._lock:
            if handle in self._handles:
                self._handles.discard(handle)
                return True
            return False


if sys.platform == "win32":
    _STATUS_CALLBACK = ctypes.WINFUNCTYPE(
        None,
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _winhttp = ctypes.WinDLL("winhttp.dll", use_last_error=True)

    _WinHttpOpen = _winhttp.WinHttpOpen
    _WinHttpOpen.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _WinHttpOpen.restype = ctypes.c_void_p

    _WinHttpSetTimeouts = _winhttp.WinHttpSetTimeouts
    _WinHttpSetTimeouts.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _WinHttpSetTimeouts.restype = wintypes.BOOL

    _WinHttpSetOption = _winhttp.WinHttpSetOption
    _WinHttpSetOption.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _WinHttpSetOption.restype = wintypes.BOOL

    _WinHttpQueryOption = _winhttp.WinHttpQueryOption
    _WinHttpQueryOption.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    _WinHttpQueryOption.restype = wintypes.BOOL

    _WinHttpConnect = _winhttp.WinHttpConnect
    _WinHttpConnect.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD]
    _WinHttpConnect.restype = ctypes.c_void_p

    _WinHttpOpenRequest = _winhttp.WinHttpOpenRequest
    _WinHttpOpenRequest.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _WinHttpOpenRequest.restype = ctypes.c_void_p

    _WinHttpSetStatusCallback = _winhttp.WinHttpSetStatusCallback
    _WinHttpSetStatusCallback.argtypes = [ctypes.c_void_p, _STATUS_CALLBACK, wintypes.DWORD, ctypes.c_size_t]
    _WinHttpSetStatusCallback.restype = ctypes.c_void_p

    _WinHttpSendRequest = _winhttp.WinHttpSendRequest
    _WinHttpSendRequest.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    _WinHttpSendRequest.restype = wintypes.BOOL

    _WinHttpReceiveResponse = _winhttp.WinHttpReceiveResponse
    _WinHttpReceiveResponse.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _WinHttpReceiveResponse.restype = wintypes.BOOL

    _WinHttpQueryHeaders = _winhttp.WinHttpQueryHeaders
    _WinHttpQueryHeaders.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _WinHttpQueryHeaders.restype = wintypes.BOOL

    _WinHttpReadData = _winhttp.WinHttpReadData
    _WinHttpReadData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _WinHttpReadData.restype = wintypes.BOOL

    _WinHttpCloseHandle = _winhttp.WinHttpCloseHandle
    _WinHttpCloseHandle.argtypes = [ctypes.c_void_p]
    _WinHttpCloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - модуль импортируется и на Linux в тестах
    _STATUS_CALLBACK = ctypes.CFUNCTYPE(
        None, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32
    )
    _winhttp = None


def is_winhttp_available() -> bool:
    return _winhttp is not None


def is_tls13_supported() -> bool:
    """Клиентский TLS 1.3 в Schannel есть с Windows 11 / Server 2022."""
    if sys.platform != "win32":
        return False
    try:
        return int(sys.getwindowsversion().build) >= 20348
    except Exception:
        return False


def _close(handle: int) -> None:
    if handle and _winhttp is not None:
        _WinHttpCloseHandle(ctypes.c_void_p(handle))


# Флаги SECURE_FAILURE по дескриптору запроса. Колбэк один на процесс и
# живёт вечно, поэтому WinHTTP не вызовет собранный ctypes-объект.
_secure_flags_lock = threading.Lock()
_secure_flags: dict[int, int] = {}


def _on_status(handle, _context, status, info, info_length) -> None:
    if int(status) != WINHTTP_CALLBACK_STATUS_SECURE_FAILURE or not info or int(info_length) < 4:
        return
    flags = ctypes.cast(info, ctypes.POINTER(wintypes.DWORD)).contents.value
    with _secure_flags_lock:
        key = int(handle or 0)
        if key in _secure_flags:
            _secure_flags[key] |= int(flags)


_STATUS_ROUTINE = _STATUS_CALLBACK(_on_status)


def _remote_ip(request: int) -> str:
    """Адрес, к которому WinHTTP реально подключился."""
    buffer = ctypes.create_string_buffer(512)
    # WINHTTP_CONNECTION_INFO: DWORD cbSize + два SOCKADDR_STORAGE, упаковка 4.
    for struct_size in (260, 264):
        struct.pack_into("<I", buffer, 0, struct_size)
        size = wintypes.DWORD(struct_size)
        if not _WinHttpQueryOption(
            ctypes.c_void_p(request),
            WINHTTP_OPTION_CONNECTION_INFO,
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(size),
        ):
            continue
        remote_offset = 4 + 128 if struct_size == 260 else 8 + 128
        family = struct.unpack_from("<H", buffer.raw, remote_offset)[0]
        if family == socket.AF_INET:
            return socket.inet_ntoa(buffer.raw[remote_offset + 4 : remote_offset + 8])
        if family == 23:  # AF_INET6 в Windows
            return socket.inet_ntop(socket.AF_INET6, buffer.raw[remote_offset + 8 : remote_offset + 24])
        return ""
    return ""


def https_request(
    host: str,
    path: str = "/",
    *,
    port: int = 443,
    method: str = "GET",
    headers: Sequence[str] = (),
    timeout: float = 5.0,
    tls_protocols: int = 0,
    max_body: int = 0,
    body_done: Callable[[bytes], bool] | None = None,
    cancel: HttpCancel | None = None,
) -> HttpsResult:
    """Выполняет один HTTPS-запрос без прокси и без перехода по редиректам.

    ``status`` заполнен, если сервер ответил любым HTTP-кодом: сам факт ответа
    значит, что соединение и шифрование прошли. Тело читается только при
    ``max_body > 0``; ``body_done`` позволяет остановиться раньше.
    """
    started = time.perf_counter()

    def _elapsed() -> float:
        return (time.perf_counter() - started) * 1000

    if _winhttp is None:
        return HttpsResult(error=-1)

    token = cancel or HttpCancel()
    timeout_ms = max(100, int(float(timeout) * 1000))
    handles: list[int] = []
    request = 0

    def _open(handle) -> int:
        value = int(handle or 0)
        if value and not token._track(value):
            _close(value)
            return 0
        if value:
            handles.append(value)
        return value

    def _fail(error: int | None = None) -> HttpsResult:
        code = ctypes.get_last_error() if error is None else error
        if token.cancelled:
            code = ERROR_WINHTTP_OPERATION_CANCELLED
        with _secure_flags_lock:
            flags = _secure_flags.get(request, 0)
        return HttpsResult(error=int(code), cert_flags=int(flags), elapsed_ms=_elapsed())

    try:
        session = _open(
            _WinHttpOpen("ZapretGUI-Diagnostics/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY, None, None, 0)
        )
        if not session:
            return _fail()
        _WinHttpSetTimeouts(ctypes.c_void_p(session), timeout_ms, timeout_ms, timeout_ms, timeout_ms)
        if tls_protocols:
            value = wintypes.DWORD(int(tls_protocols))
            if not _WinHttpSetOption(
                ctypes.c_void_p(session),
                WINHTTP_OPTION_SECURE_PROTOCOLS,
                ctypes.byref(value),
                ctypes.sizeof(value),
            ):
                return HttpsResult(error=-1, elapsed_ms=_elapsed())

        connection = _open(_WinHttpConnect(ctypes.c_void_p(session), host, int(port), 0))
        if not connection:
            return _fail()

        request = _open(
            _WinHttpOpenRequest(
                ctypes.c_void_p(connection),
                method,
                path or "/",
                None,
                None,
                None,
                WINHTTP_FLAG_SECURE,
            )
        )
        if not request:
            return _fail()

        with _secure_flags_lock:
            _secure_flags[request] = 0
        _WinHttpSetStatusCallback(
            ctypes.c_void_p(request),
            _STATUS_ROUTINE,
            WINHTTP_CALLBACK_FLAG_SECURE_FAILURE,
            0,
        )
        no_redirects = wintypes.DWORD(WINHTTP_DISABLE_REDIRECTS)
        _WinHttpSetOption(
            ctypes.c_void_p(request),
            WINHTTP_OPTION_DISABLE_FEATURE,
            ctypes.byref(no_redirects),
            ctypes.sizeof(no_redirects),
        )

        header_text = "\r\n".join(str(item) for item in headers if item) or None
        if not _WinHttpSendRequest(
            ctypes.c_void_p(request),
            header_text,
            0xFFFFFFFF if header_text else 0,
            None,
            0,
            0,
            0,
        ):
            return _fail()
        if not _WinHttpReceiveResponse(ctypes.c_void_p(request), None):
            return _fail()

        status_code = wintypes.DWORD(0)
        status_size = wintypes.DWORD(ctypes.sizeof(status_code))
        if not _WinHttpQueryHeaders(
            ctypes.c_void_p(request),
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            None,
            ctypes.byref(status_code),
            ctypes.byref(status_size),
            None,
        ):
            return _fail()

        remote_ip = _remote_ip(request)
        body = bytearray()
        read_error = 0
        if max_body > 0:
            chunk = ctypes.create_string_buffer(32 * 1024)
            while len(body) < max_body:
                read = wintypes.DWORD(0)
                if not _WinHttpReadData(
                    ctypes.c_void_p(request),
                    chunk,
                    min(ctypes.sizeof(chunk), max_body - len(body)),
                    ctypes.byref(read),
                ):
                    read_error = ctypes.get_last_error() or ERROR_WINHTTP_CONNECTION_ERROR
                    break
                if not read.value:
                    break
                body.extend(chunk.raw[: read.value])
                if body_done is not None and body_done(bytes(body)):
                    break

        return HttpsResult(
            status=int(status_code.value),
            remote_ip=remote_ip,
            body=bytes(body),
            read_error=int(read_error),
            elapsed_ms=_elapsed(),
        )
    finally:
        with _secure_flags_lock:
            _secure_flags.pop(request, None)
        for handle in reversed(handles):
            if token._release(handle):
                _close(handle)
