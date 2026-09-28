"""HTTPS-запрос к конкретному адресу тем же способом, что и подбор стратегий.

Зачем не WinHTTP
----------------
WinHTTP на Windows 10 умеет только TLS 1.2 и отправляет приветствие Schannel,
совсем не похожее на браузерное. Стратегии Zapret подбираются под браузеры и
Discord (TLS 1.3), поэтому с включённым пресетом браузер открывал YouTube, а
WinHTTP-проверка показывала «блокировку DPI». Здесь запрос идёт через OpenSSL
(TLS 1.3, как у браузера) — тем же клиентом, которым подбор стратегий
проверяет, помогает ли стратегия.

Адрес задаётся явно: диагностика сама решает, к какому IP идти (из DNS
системы, из hosts или из эталона DNS-over-HTTPS), а имя сайта уходит в SNI и
в заголовок Host. Сертификат проверяется для имени сайта, поэтому чужой
сервер по подменённому адресу виден сразу.

Отмена: ``ProbeCancel.cancel()`` закрывает сокеты из другого потока, и
блокирующий вызов сразу возвращается.
"""

from __future__ import annotations

import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "KIND_CANCELLED",
    "KIND_CERT",
    "KIND_CONNECT",
    "KIND_ERROR",
    "KIND_OK",
    "KIND_RESET",
    "KIND_TIMEOUT",
    "KIND_TLS",
    "ProbeCancel",
    "ProbeResult",
    "https_get",
]

# Виды исхода запроса. Выводы диагностики опираются только на них.
KIND_OK = "ok"
KIND_CERT = "cert"
KIND_TLS = "tls"
KIND_RESET = "reset"
KIND_TIMEOUT = "timeout"
KIND_CONNECT = "connect"
KIND_CANCELLED = "cancelled"
KIND_ERROR = "error"

STAGE_CONNECT = "connect"
STAGE_TLS = "tls"
STAGE_READ = "read"

_BROWSER_HEADERS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language: en-US,en;q=0.9",
    "Accept-Encoding: identity",
)

_STATUS_LINE = re.compile(rb"^HTTP/\d(?:\.\d)?\s+(\d{3})")

# Коды проверки сертификата OpenSSL.
_X509_HOSTNAME_MISMATCH = 62
_X509_EXPIRED = 10
_X509_NOT_YET_VALID = 9
_X509_SELF_SIGNED = (18, 19)
_X509_UNKNOWN_ISSUER = (2, 20, 21)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ip: str
    kind: str
    stage: str = ""
    status: int | None = None
    body_size: int = 0
    # Ответ начал приходить, но оборвался (таймаут или сброс посреди тела).
    body_cut: bool = False
    cert_problem: str = ""
    tls_version: str = ""
    elapsed_ms: float = 0.0
    detail: str = ""
    body: bytes = b""

    @property
    def ok(self) -> bool:
        return self.kind == KIND_OK


class ProbeCancel:
    """Общая кнопка «Стоп» для пачки запросов из разных потоков."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: set[socket.socket] = set()
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            sockets = list(self._sockets)
            self._sockets.clear()
        for sock in sockets:
            _close_quietly(sock)

    def _track(self, sock: socket.socket) -> bool:
        with self._lock:
            if self._cancelled:
                return False
            self._sockets.add(sock)
            return True

    def _release(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(sock)


def _close_quietly(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


_context_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def _client_context() -> ssl.SSLContext:
    """Один контекст на процесс: загрузка хранилища сертификатов не бесплатна."""
    global _context
    with _context_lock:
        if _context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_default_certs()
            # В Python на Windows хранилище Windows может быть неполным
            # (корневые сертификаты догружаются по требованию), и без certifi
            # подлинный сайт выглядел бы «сертификатом неизвестного центра».
            try:
                import certifi

                context.load_verify_locations(certifi.where())
            except Exception:
                pass
            try:
                context.set_alpn_protocols(["http/1.1"])
            except (NotImplementedError, ssl.SSLError):
                pass
            _context = context
        return _context


def _cert_problem(error: ssl.SSLCertVerificationError) -> str:
    code = int(getattr(error, "verify_code", 0) or 0)
    if code == _X509_HOSTNAME_MISMATCH:
        return "сертификат выдан другому сайту"
    if code in (_X509_EXPIRED, _X509_NOT_YET_VALID):
        return "сертификат просрочен или ещё не действует (проверьте часы Windows)"
    if code in _X509_SELF_SIGNED:
        return "самодельный сертификат"
    if code in _X509_UNKNOWN_ISSUER:
        return "сертификат выдан неизвестным центром"
    return "сертификат не прошёл проверку"


def _parse_status(data: bytes) -> int | None:
    match = _STATUS_LINE.match(data)
    return int(match.group(1)) if match else None


def https_get(
    host: str,
    ip: str,
    path: str = "/",
    *,
    timeout: float,
    port: int = 443,
    read_limit: int = 0,
    read_timeout: float = 3.0,
    body_done: Callable[[bytes], bool] | None = None,
    headers: Sequence[str] = _BROWSER_HEADERS,
    cancel: ProbeCancel | None = None,
) -> ProbeResult:
    """GET ``https://host/path`` по адресу ``ip`` (SNI и Host — ``host``).

    ``timeout`` — общий бюджет на подключение, шифрование и первый ответ.
    ``read_limit`` — сколько байт ответа читать (0 — только заголовок, чтобы
    понять, что сервер ответил). ``read_timeout`` — сколько ждать следующих
    данных: оборванное на середине тело видно как ``body_cut``.
    """
    started = time.perf_counter()
    deadline = started + max(0.2, float(timeout))
    token = cancel or ProbeCancel()
    stage = STAGE_CONNECT
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    response = bytearray()
    status: int | None = None
    tls_version = ""

    def _elapsed() -> float:
        return (time.perf_counter() - started) * 1000

    def _remaining() -> float:
        return max(0.05, deadline - time.perf_counter())

    def _result(kind: str, detail: str = "", **extra) -> ProbeResult:
        if token.cancelled and kind != KIND_OK:
            kind = KIND_CANCELLED
        return ProbeResult(
            ip=ip,
            kind=kind,
            stage=stage,
            status=status,
            body_size=len(response),
            tls_version=tls_version,
            elapsed_ms=_elapsed(),
            detail=detail,
            body=bytes(response),
            **extra,
        )

    try:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        if not token._track(sock):
            return _result(KIND_CANCELLED)
        sock.settimeout(_remaining())
        sock.connect((ip, int(port)))

        stage = STAGE_TLS
        wrapped = _client_context().wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
        token._track(wrapped)
        wrapped.settimeout(_remaining())
        wrapped.do_handshake()
        tls_version = str(wrapped.version() or "")

        stage = STAGE_READ
        lines = [f"GET {path or '/'} HTTP/1.1", f"Host: {host}", "Connection: close", *headers, "", ""]
        wrapped.sendall("\r\n".join(lines).encode("ascii", errors="ignore"))

        wrapped.settimeout(_remaining())
        while True:
            chunk = wrapped.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
            if status is None:
                status = _parse_status(bytes(response[:32]))
                if status is None and len(response) >= 32:
                    return _result(KIND_ERROR, "сервер ответил не по HTTP")
            if status is not None:
                if len(response) >= max(read_limit, 1):
                    break
                if body_done is not None and body_done(bytes(response)):
                    break
                # Дальше тело: ждём по ``read_timeout`` на каждый кусок.
                wrapped.settimeout(max(0.2, float(read_timeout)))
        if status is None:
            return _result(KIND_RESET, "сервер закрыл соединение без ответа")
        return _result(KIND_OK)
    except ssl.SSLCertVerificationError as error:
        return _result(KIND_CERT, str(error), cert_problem=_cert_problem(error))
    except (socket.timeout, TimeoutError):
        if status is not None:
            return _result(KIND_OK, body_cut=True)
        if stage == STAGE_CONNECT:
            return _result(KIND_CONNECT, "сервер не отвечает на подключение")
        return _result(KIND_TIMEOUT)
    except ConnectionRefusedError:
        return _result(KIND_CONNECT, "подключение отклонено")
    except ssl.SSLError as error:
        if status is not None:
            return _result(KIND_OK, body_cut=True)
        return _result(KIND_TLS, str(error))
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as error:
        if status is not None:
            return _result(KIND_OK, body_cut=True)
        return _result(KIND_RESET, str(error))
    except OSError as error:
        if status is not None:
            return _result(KIND_OK, body_cut=True)
        if stage == STAGE_CONNECT:
            return _result(KIND_CONNECT, str(error))
        return _result(KIND_RESET, str(error))
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token._release(item)
                _close_quietly(item)
