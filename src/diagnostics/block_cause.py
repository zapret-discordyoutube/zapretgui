"""Почему сайт не открылся: уточнение причины дополнительными пробами.

Основная проверка (``diagnostics.tls_probe``) говорит, на какой ступени
оборвалось соединение. Этого мало, чтобы понять, **как** блокируют: по имени
сайта или по адресу. Здесь делаются три дешёвые пробы к тому же адресу.

1. **Тот же адрес, другое имя.** В начале шифрованного соединения браузер
   открытым текстом называет сайт, к которому идёт (это поле зовётся SNI).
   Если с именем сайта соединение рвётся, а с посторонним именем или вовсе
   без имени тот же сервер отвечает — режут по имени. Ответом считается и
   отказ сервера («не знаю такого имени»): раз он дошёл, дорога открыта.
2. **Обычный HTTP, порт 80.** Часть провайдеров вместо сайта показывает
   страницу о блокировке или отдаёт код 451.
3. **Пинг** — когда адрес не принимает соединение вовсе.

Сбор (``collect``) отделён от вывода (``judge``): вывод — чистая функция над
собранными фактами.
"""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from diagnostics.tls_probe import (
    KIND_CONNECT,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    STAGE_CONNECT,
    STAGE_TLS,
    ProbeResult,
)
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "CAUSE_ADDRESS_CLOSED",
    "CAUSE_ADDRESS_SILENT",
    "CAUSE_BY_ADDRESS",
    "CAUSE_BY_NAME",
    "CAUSE_STUB_PAGE",
    "HELLO_ALERT",
    "HELLO_CANCELLED",
    "HELLO_CONNECT",
    "HELLO_GARBAGE",
    "HELLO_OK",
    "HELLO_RESET",
    "HELLO_TIMEOUT",
    "NEUTRAL_NAME",
    "Cause",
    "CauseFacts",
    "HelloResult",
    "HttpFacts",
    "collect",
    "http_probe",
    "judge",
    "needs_refining",
    "stub_reason",
    "tls_hello",
]

# Постороннее имя: существует, ни в одном списке блокировок его нет.
NEUTRAL_NAME = "example.com"

HELLO_OK = "ok"  # шифрование установилось
HELLO_ALERT = "alert"  # сервер отказал сам — но ответил
HELLO_RESET = "reset"
HELLO_TIMEOUT = "timeout"
HELLO_CONNECT = "connect"  # не удалось даже соединиться
HELLO_GARBAGE = "garbage"  # пришло не шифрование: ответил не сервер
HELLO_CANCELLED = "cancelled"

CAUSE_STUB_PAGE = "stub_page"
CAUSE_BY_NAME = "by_name"
CAUSE_BY_ADDRESS = "by_address"
CAUSE_ADDRESS_CLOSED = "address_closed"
CAUSE_ADDRESS_SILENT = "address_silent"

HELLO_TIMEOUT_S = 4.0
HTTP_TIMEOUT_S = 4.0
_HTTP_READ_LIMIT = 4096
_STATUS_UNAVAILABLE_FOR_LEGAL_REASONS = 451

# Страницы-заглушки операторов: куда уводит перенаправление.
_STUB_HOSTS = (
    "warning.rt.ru",
    "block.mts.ru",
    "blocked.beeline.ru",
    "block.megafon.ru",
    "rkn.gov.ru",
)
# Признаки в тексте страницы. Только такие, которых на обычном сайте не бывает:
# слова вроде «заблокирован» встречаются и на обычных страницах.
_STUB_MARKERS = (
    b"eais.rkn.gov.ru",
    b"blocklist.rkn.gov.ru",
    b"nap.rkn.gov.ru",
    b"149-\xd1\x84\xd0\xb7",  # «149-фз»
    b"149-fz",
)


@dataclass(frozen=True, slots=True)
class HelloResult:
    kind: str
    detail: str = ""

    @property
    def answered(self) -> bool:
        """Сервер по этому адресу ответил — значит, дорога до него открыта."""
        return self.kind in (HELLO_OK, HELLO_ALERT)


@dataclass(frozen=True, slots=True)
class HttpFacts:
    """Что ответил порт 80. ``status`` None — ответа не было."""

    status: int | None = None
    location: str = ""
    body: bytes = b""


@dataclass(frozen=True, slots=True)
class CauseFacts:
    """Всё, что удалось узнать дополнительно. Выводов здесь нет."""

    host: str
    result: ProbeResult
    neutral: HelloResult | None = None
    nameless: HelloResult | None = None
    http: HttpFacts | None = None
    # None — пинг не делали или он недоступен в системе.
    ping_ok: bool | None = None


@dataclass(frozen=True, slots=True)
class Cause:
    code: str
    text: str
    # Уверенный вывод опирается на прямое противоречие, а не на отсутствие ответа.
    confident: bool = False


_context_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def _hello_context() -> ssl.SSLContext:
    """Тот же клиент, что у основной проверки, но без проверки сертификата.

    Сертификат здесь не важен: с посторонним именем сервер и должен
    предъявить «не тот». Важно одно — ответил ли он вообще.
    """
    global _context
    with _context_lock:
        if _context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            try:
                context.set_alpn_protocols(["http/1.1"])
            except (NotImplementedError, ssl.SSLError):
                pass
            _context = context
        return _context


def _hello_failure(error: ssl.SSLError) -> HelloResult:
    reason = str(getattr(error, "reason", "") or "")
    text = f"{reason} {error}".upper()
    if isinstance(error, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return HelloResult(HELLO_RESET, "соединение закрыто на приветствии")
    if "WRONG_VERSION_NUMBER" in text or "UNKNOWN_PROTOCOL" in text or "RECORD_LAYER" in text:
        return HelloResult(HELLO_GARBAGE, reason)
    if "ALERT" in text or "UNRECOGNIZED_NAME" in text or "HANDSHAKE_FAILURE" in text:
        return HelloResult(HELLO_ALERT, reason)
    return HelloResult(HELLO_GARBAGE, reason or str(error))


def tls_hello(
    ip: str,
    name: str | None,
    *,
    port: int = 443,
    timeout: float = HELLO_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> HelloResult:
    """Начинает шифрованное соединение с ``ip``, называя имя ``name`` (None — без имени)."""
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    connected = False
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return HelloResult(HELLO_CANCELLED)
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        connected = True
        wrapped = _hello_context().wrap_socket(sock, server_hostname=name, do_handshake_on_connect=False)
        token.track(wrapped)
        wrapped.settimeout(timeout)
        wrapped.do_handshake()
        return HelloResult(HELLO_OK)
    except (socket.timeout, TimeoutError):
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_TIMEOUT if connected else HELLO_CONNECT)
    except ssl.SSLError as error:
        return HelloResult(HELLO_CANCELLED) if token.cancelled else _hello_failure(error)
    except OSError as error:
        if token.cancelled:
            return HelloResult(HELLO_CANCELLED)
        return HelloResult(HELLO_RESET if connected else HELLO_CONNECT, str(error))
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token.release(item)
                close_quietly(item)


def http_probe(
    host: str,
    ip: str,
    *,
    port: int = 80,
    timeout: float = HTTP_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> HttpFacts:
    """``GET http://host/`` по адресу ``ip``: код ответа, куда перенаправили и начало страницы."""
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    data = bytearray()
    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return HttpFacts()
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        request = f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: Mozilla/5.0\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        sock.sendall(request.encode("ascii", errors="ignore"))
        while len(data) < _HTTP_READ_LIMIT:
            chunk = sock.recv(_HTTP_READ_LIMIT - len(data))
            if not chunk:
                break
            data.extend(chunk)
    except OSError:
        pass
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)
    if token.cancelled:
        return HttpFacts()

    head, _separator, body = bytes(data).partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    parts = lines[0].split(None, 2) if lines else []
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/") or not parts[1].isdigit():
        return HttpFacts()
    location = ""
    for line in lines[1:]:
        name, _colon, value = line.partition(b":")
        if name.strip().lower() == b"location":
            location = value.strip().decode("latin-1", errors="replace")
    return HttpFacts(status=int(parts[1]), location=location, body=body)


def _same_site(host: str, other: str) -> bool:
    host, other = host.lower().rstrip("."), other.lower().rstrip(".")
    return other == host or other.endswith("." + host) or host.endswith("." + other)


def stub_reason(host: str, facts: HttpFacts | None) -> str:
    """Почему ответ порта 80 — страница о блокировке. Пусто, если это обычный ответ."""
    if facts is None or facts.status is None:
        return ""
    if facts.status == _STATUS_UNAVAILABLE_FOR_LEGAL_REASONS:
        return "код 451 «недоступно по юридическим причинам»"
    target = (urlsplit(facts.location).hostname or "").lower() if facts.location else ""
    if target and not _same_site(host, target):
        for stub in _STUB_HOSTS:
            if target == stub or target.endswith("." + stub):
                return f"перенаправление на страницу о блокировке {target}"
    lowered = facts.body.lower()
    for marker in _STUB_MARKERS:
        if marker in lowered:
            return "страница со ссылкой на реестр блокировок"
    return ""


def needs_refining(result: ProbeResult | None) -> bool:
    """Есть ли что уточнять: соединение не состоялось или оборвалось на шифровании."""
    if result is None or not result.ip:
        return False
    if result.kind == KIND_CONNECT:
        return True
    return result.kind in (KIND_RESET, KIND_TIMEOUT, KIND_TLS) and result.stage == STAGE_TLS


def collect(
    host: str,
    result: ProbeResult,
    *,
    submit: Callable,
    cancel: SocketCancel,
    ping: Callable[[str], bool | None] | None = None,
) -> CauseFacts:
    """Дополнительные пробы к адресу, на котором сайт не открылся. Идут одновременно."""
    ip = result.ip
    http = submit(http_probe, host, ip, cancel=cancel)
    if result.kind == KIND_CONNECT:
        ping_future = submit(ping, ip) if ping is not None else None
        return CauseFacts(
            host=host,
            result=result,
            http=http.result(),
            ping_ok=ping_future.result() if ping_future is not None else None,
        )
    neutral = submit(tls_hello, ip, NEUTRAL_NAME, cancel=cancel)
    nameless = submit(tls_hello, ip, None, cancel=cancel)
    return CauseFacts(host=host, result=result, neutral=neutral.result(), nameless=nameless.result(), http=http.result())


def _how(result: ProbeResult) -> str:
    return "молча пропадает" if result.kind == KIND_TIMEOUT else "обрывается"


def judge(facts: CauseFacts) -> Cause | None:
    """Вывод о способе блокировки или None, если фактов для него не хватает."""
    stub = stub_reason(facts.host, facts.http)
    if stub:
        return Cause(CAUSE_STUB_PAGE, f"провайдер показывает страницу о блокировке: {stub}", confident=True)

    result = facts.result
    if result.kind == KIND_CONNECT and result.stage == STAGE_CONNECT:
        if facts.http is not None and facts.http.status is not None:
            # Тот же адрес ответил по обычному порту: сервер жив, дорога до него есть.
            return Cause(
                CAUSE_ADDRESS_CLOSED,
                "тот же адрес отвечает по обычному порту 80, а шифрованное соединение (порт 443) "
                "не принимает — на этом адресе закрыт именно защищённый доступ",
                confident=True,
            )
        if facts.ping_ok:
            return Cause(
                CAUSE_ADDRESS_CLOSED,
                "адрес отвечает на пинг, но соединение не принимает — он закрыт для соединений "
                "(так выглядит блокировка по адресу и режим «белых списков»)",
            )
        if facts.ping_ok is False:
            return Cause(CAUSE_ADDRESS_SILENT, "адрес не отвечает совсем — ни на соединение, ни на пинг")
        return None

    hellos = [item for item in (facts.neutral, facts.nameless) if item is not None and item.kind != HELLO_CANCELLED]
    if not hellos:
        return None
    if any(item.answered for item in hellos):
        other = "с посторонним именем" if facts.neutral is not None and facts.neutral.answered else "без имени"
        return Cause(
            CAUSE_BY_NAME,
            f"блокировка по имени сайта: с именем {facts.host} соединение {_how(result)}, "
            f"а {other} тот же сервер отвечает",
            confident=True,
        )
    if all(item.kind in (HELLO_RESET, HELLO_TIMEOUT) for item in hellos):
        return Cause(
            CAUSE_BY_ADDRESS,
            "шифрование не устанавливается ни с каким именем — закрыт сам адрес или вся его сеть, "
            "а не имя сайта",
        )
    return None
