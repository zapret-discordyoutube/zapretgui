"""Запрос к конкретному DNS-серверу напрямую, без системного резолвера.

Windows умеет спрашивать только «свои» DNS-серверы, а для сравнения ответов
нужно спросить именно выбранный: 8.8.8.8, 1.1.1.1 и так далее. Поэтому запрос
собирается и разбирается вручную (формат RFC 1035).

Способов связи четыре, и у каждого своя функция, потому что провайдер может
закрыть любой из них отдельно:

* ``query_udp`` — обычный DNS, порт 53;
* ``query_tcp`` — тот же DNS по TCP, порт 53;
* ``query_dot`` — шифрованный DNS поверх TLS, порт 853;
* ``query_doh`` — шифрованный DNS поверх HTTPS, порт 443.

``query_server`` ведёт себя как обычная программа: UDP, а если ответ не
поместился в пакет — TCP. Сетевой сбой — это не исключение, а результат с
видом сбоя (``failure``): так вывод «сервер молчит» отличается от «порт
закрыт» и «сертификат чужой». Запрос можно снять из другого потока через
``SocketCancel``.

Сторонняя библиотека dnspython здесь невозможна: её пакет называется ``dns`` и
перекрывается собственным ``src/dns``.
"""

from __future__ import annotations

import errno
import os
import socket
import struct
import time
from dataclasses import dataclass
from ipaddress import ip_address

from utils.socket_cancel import SocketCancel, close_quietly

TYPE_A = 1
TYPE_NS = 2
TYPE_CNAME = 5
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_AAAA = 28

RCODE_OK = 0
RCODE_NXDOMAIN = 3

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_NXDOMAIN = "nxdomain"
STATUS_REFUSED = "refused"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"

# Каким способом шёл запрос.
TRANSPORT_UDP = "udp"
TRANSPORT_TCP = "tcp"
TRANSPORT_DOT = "dot"
TRANSPORT_DOH = "doh"

# Почему ответа нет. Выводы проверок опираются на эти виды, а не на текст ошибки.
FAILURE_TIMEOUT = "timeout"          # сервер молчит, пакеты пропадают
FAILURE_REFUSED = "refused"          # на этом порту никто не слушает
FAILURE_RESET = "reset"              # соединение оборвано по пути или сервером
FAILURE_CLOSED = "closed"            # сервер закрыл соединение, не ответив
FAILURE_UNREACHABLE = "unreachable"  # до адреса нет дороги
FAILURE_TLS = "tls"                  # шифрование не установилось
FAILURE_CERT = "cert"                # сервер предъявил чужой сертификат
FAILURE_HTTP = "http"                # сервер ответил ошибкой HTTP
FAILURE_MALFORMED = "malformed"      # пришло не то, что должно
FAILURE_CANCELLED = "cancelled"
FAILURE_OTHER = "other"

DEFAULT_TIMEOUT_S = 2.0
# Шифрованный запрос — это ещё соединение и рукопожатие TLS.
ENCRYPTED_TIMEOUT_S = 4.0
# WSAENETUNREACH, WSAEHOSTUNREACH: Python не всегда переводит их в errno.
_UNREACHABLE_WINERRORS = frozenset({10051, 10065})
_UNREACHABLE_ERRNOS = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH})
# Больше 512 байт по UDP сервер пришлёт, только если мы об этом попросили (EDNS0).
_EDNS_UDP_SIZE = 1232
_MAX_POINTER_HOPS = 32
_FLAG_QR = 0x8000
_FLAG_TC = 0x0200


_FAILURE_TEXT = {
    FAILURE_TIMEOUT: "сервер молчит",
    FAILURE_REFUSED: "порт закрыт",
    FAILURE_RESET: "соединение оборвано",
    FAILURE_CLOSED: "сервер закрыл соединение, не ответив",
    FAILURE_UNREACHABLE: "до адреса нет дороги",
    FAILURE_TLS: "шифрование не установилось",
    FAILURE_CERT: "сервер предъявил чужой сертификат",
    FAILURE_MALFORMED: "пришёл не DNS-ответ",
    FAILURE_CANCELLED: "проверка остановлена",
}


class DnsWireError(ValueError):
    """Ответ сервера не разбирается как DNS-пакет."""


class _ConnectionClosed(DnsWireError):
    """Сервер закрыл соединение, не прислав ответ целиком."""


@dataclass(frozen=True, slots=True)
class DnsRecord:
    name: str
    rtype: int
    ttl: int
    value: str


@dataclass(frozen=True, slots=True)
class DnsMessage:
    query_id: int
    is_response: bool
    truncated: bool
    rcode: int
    question: str
    answers: tuple[DnsRecord, ...]


@dataclass(frozen=True, slots=True)
class DnsQueryResult:
    """Итог одного запроса. Сетевые ошибки — это статус, а не исключение."""

    status: str
    rcode: int | None = None
    records: tuple[DnsRecord, ...] = ()
    elapsed_ms: float | None = None
    detail: str = ""
    transport: str = ""
    # Вид сбоя (FAILURE_*), если ответа нет; пусто, если сервер ответил.
    failure: str = ""
    # Ответ не поместился в пакет UDP (флаг TC).
    truncated: bool = False

    @property
    def answered(self) -> bool:
        """Сервер ответил хоть что-то (включая «такого домена нет»)."""
        return self.rcode is not None

    def values(self, rtype: int) -> tuple[str, ...]:
        seen: list[str] = []
        for record in self.records:
            if record.rtype == rtype and record.value not in seen:
                seen.append(record.value)
        return tuple(seen)


def failure_text(result: DnsQueryResult) -> str:
    """Почему сервер не ответил — словами для отчёта. Пусто, если он ответил."""
    if result.answered or not result.failure:
        return ""
    if result.failure == FAILURE_HTTP:
        return f"сервер ответил ошибкой ({result.detail})" if result.detail else "сервер ответил ошибкой"
    return _FAILURE_TEXT.get(result.failure) or result.detail or "запрос не выполнился"


def encode_name(name: str) -> bytes:
    """Имя в формате DNS: метки с длиной впереди. Кириллица — через punycode."""
    cleaned = str(name or "").strip().strip(".")
    if not cleaned:
        raise DnsWireError("пустое имя")
    encoded = b""
    for label in cleaned.split("."):
        try:
            raw = label.encode("ascii")
        except UnicodeEncodeError:
            try:
                raw = label.encode("idna")
            except UnicodeError as exc:
                raise DnsWireError(f"недопустимое имя: {name}") from exc
        if not raw or len(raw) > 63:
            raise DnsWireError(f"недопустимое имя: {name}")
        encoded += bytes([len(raw)]) + raw
    if len(encoded) > 253:
        raise DnsWireError(f"слишком длинное имя: {name}")
    return encoded + b"\x00"


def build_query(query_id: int, name: str, rtype: int) -> bytes:
    """DNS-запрос с флагом «рекурсия нужна» и разрешением на большой ответ."""
    header = struct.pack("!HHHHHH", query_id & 0xFFFF, 0x0100, 1, 0, 0, 1)
    question = encode_name(name) + struct.pack("!HH", rtype, 1)
    opt = b"\x00" + struct.pack("!HHIH", 41, _EDNS_UDP_SIZE, 0, 0)
    return header + question + opt


def reverse_name(ip: str) -> str:
    """Имя для обратного запроса: 1.2.3.4 → 4.3.2.1.in-addr.arpa."""
    address = ip_address(str(ip).strip())
    return address.reverse_pointer


def _read_name(packet: bytes, offset: int) -> tuple[str, int]:
    """Читает имя с учётом сжатия (ссылок на уже встречавшиеся имена)."""
    labels: list[str] = []
    end: int | None = None
    hops = 0
    while True:
        if offset >= len(packet):
            raise DnsWireError("имя выходит за границы пакета")
        length = packet[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise DnsWireError("оборванная ссылка в имени")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | packet[offset + 1]
            hops += 1
            if hops > _MAX_POINTER_HOPS:
                raise DnsWireError("зацикленные ссылки в имени")
            continue
        if length & 0xC0:
            raise DnsWireError("неизвестный тип метки")
        start = offset + 1
        if start + length > len(packet):
            raise DnsWireError("метка выходит за границы пакета")
        labels.append(packet[start : start + length].decode("ascii", "replace"))
        offset = start + length
    return ".".join(labels), (end if end is not None else offset)


def _read_txt(data: bytes) -> str:
    chunks: list[str] = []
    index = 0
    while index < len(data):
        size = data[index]
        chunks.append(data[index + 1 : index + 1 + size].decode("utf-8", "replace"))
        index += 1 + size
    return "".join(chunks)


def parse_response(packet: bytes) -> DnsMessage:
    """Разбирает ответ: код результата и записи раздела «ответы»."""
    if len(packet) < 12:
        raise DnsWireError("слишком короткий пакет")
    query_id, flags, questions, answers, _authority, _additional = struct.unpack("!HHHHHH", packet[:12])
    offset = 12
    question = ""
    for index in range(questions):
        name, offset = _read_name(packet, offset)
        offset += 4
        if index == 0:
            question = name.lower()

    records: list[DnsRecord] = []
    for _ in range(answers):
        name, offset = _read_name(packet, offset)
        if offset + 10 > len(packet):
            raise DnsWireError("оборванная запись")
        rtype, _rclass, ttl, size = struct.unpack("!HHIH", packet[offset : offset + 10])
        offset += 10
        if offset + size > len(packet):
            raise DnsWireError("данные записи выходят за границы пакета")
        data = packet[offset : offset + size]
        value: str | None = None
        if rtype == TYPE_A and size == 4:
            value = socket.inet_ntoa(data)
        elif rtype == TYPE_AAAA and size == 16:
            value = socket.inet_ntop(socket.AF_INET6, data)
        elif rtype in (TYPE_CNAME, TYPE_PTR, TYPE_NS):
            # Имя внутри записи тоже может ссылаться на начало пакета.
            value = _read_name(packet, offset)[0].lower()
        elif rtype == TYPE_TXT:
            value = _read_txt(data)
        offset += size
        if value is not None:
            records.append(DnsRecord(name=name.lower(), rtype=rtype, ttl=int(ttl), value=value))

    return DnsMessage(
        query_id=query_id,
        is_response=bool(flags & _FLAG_QR),
        truncated=bool(flags & _FLAG_TC),
        rcode=flags & 0x000F,
        question=question,
        answers=tuple(records),
    )


def _family(server: str) -> int:
    return socket.AF_INET6 if ip_address(server).version == 6 else socket.AF_INET


class _Cancelled(Exception):
    pass


def _failure_for(exc: BaseException) -> str:
    """Вид сбоя по исключению. Порядок важен: частные случаи идут раньше общих."""
    import ssl

    if isinstance(exc, _Cancelled):
        return FAILURE_CANCELLED
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return FAILURE_TIMEOUT
    if isinstance(exc, ssl.SSLCertVerificationError):
        return FAILURE_CERT
    if isinstance(exc, ssl.SSLError):
        return FAILURE_TLS
    if isinstance(exc, ConnectionRefusedError):
        return FAILURE_REFUSED
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return FAILURE_RESET
    if isinstance(exc, _ConnectionClosed):
        return FAILURE_CLOSED
    if isinstance(exc, DnsWireError):
        return FAILURE_MALFORMED
    if isinstance(exc, OSError) and (
        exc.errno in _UNREACHABLE_ERRNOS or getattr(exc, "winerror", None) in _UNREACHABLE_WINERRORS
    ):
        return FAILURE_UNREACHABLE
    return FAILURE_OTHER


def _failed(transport: str, exc: BaseException, cancel: SocketCancel | None) -> DnsQueryResult:
    # Отмена закрывает сокет из другого потока, и вызов падает обычной
    # сетевой ошибкой: без этой проверки «Стоп» выглядел бы сбросом соединения.
    failure = FAILURE_CANCELLED if cancel is not None and cancel.cancelled else _failure_for(exc)
    if failure == FAILURE_TIMEOUT:
        return DnsQueryResult(status=STATUS_TIMEOUT, transport=transport, failure=failure)
    detail = "" if failure == FAILURE_CANCELLED else (str(exc) or type(exc).__name__)
    return DnsQueryResult(status=STATUS_ERROR, transport=transport, failure=failure, detail=detail)


def _answered(transport: str, message: DnsMessage, started: float) -> DnsQueryResult:
    return DnsQueryResult(
        status=_status_for(message),
        rcode=message.rcode,
        records=message.answers,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        detail=f"rcode {message.rcode}" if message.rcode not in (RCODE_OK, RCODE_NXDOMAIN) else "",
        transport=transport,
        truncated=message.truncated,
    )


def _status_for(message: DnsMessage) -> str:
    if message.rcode == RCODE_NXDOMAIN:
        return STATUS_NXDOMAIN
    if message.rcode != RCODE_OK:
        return STATUS_REFUSED
    return STATUS_OK if message.answers else STATUS_EMPTY


def _prepare(server: str, name: str, rtype: int, transport: str) -> tuple[int, bytes] | DnsQueryResult:
    try:
        _family(server)
        query_id = int.from_bytes(os.urandom(2), "big")
        return query_id, build_query(query_id, name, rtype)
    except (ValueError, DnsWireError) as exc:
        return DnsQueryResult(status=STATUS_ERROR, transport=transport, failure=FAILURE_OTHER, detail=str(exc))


class _Deadline:
    """Общий предел времени на запрос: каждый шаг получает только остаток."""

    def __init__(self, timeout_s: float) -> None:
        self._until = time.perf_counter() + max(0.0, float(timeout_s))

    def remaining(self) -> float:
        left = self._until - time.perf_counter()
        if left <= 0:
            raise socket.timeout()
        return left


def _open(sock_type: int, server: str, cancel: SocketCancel | None) -> socket.socket:
    sock = socket.socket(_family(server), sock_type)
    if cancel is not None and not cancel.track(sock):
        sock.close()
        raise _Cancelled()
    return sock


def _close(sock: socket.socket | None, cancel: SocketCancel | None) -> None:
    if sock is None:
        return
    if cancel is not None:
        cancel.release(sock)
    close_quietly(sock)


def _recv_exact(sock: socket.socket, size: int, deadline: _Deadline) -> bytes:
    data = b""
    while len(data) < size:
        sock.settimeout(deadline.remaining())
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise _ConnectionClosed("сервер закрыл соединение")
        data += chunk
    return data


def _exchange_framed(sock: socket.socket, query: bytes, query_id: int, deadline: _Deadline) -> DnsMessage:
    """Запрос и ответ с двухбайтовой длиной впереди: так DNS идёт по TCP и по TLS."""
    sock.settimeout(deadline.remaining())
    sock.sendall(struct.pack("!H", len(query)) + query)
    size = struct.unpack("!H", _recv_exact(sock, 2, deadline))[0]
    message = parse_response(_recv_exact(sock, size, deadline))
    if message.query_id != (query_id & 0xFFFF) or not message.is_response:
        raise DnsWireError("ответ не на наш запрос")
    return message


def query_udp(
    server: str,
    name: str,
    rtype: int,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    port: int = 53,
    cancel: SocketCancel | None = None,
) -> DnsQueryResult:
    """Один запрос по UDP, без перехода на TCP.

    Усечённый ответ (флаг TC) возвращается как есть, с ``truncated=True``:
    проверке способов связи важно знать, что ответил именно UDP.
    """
    prepared = _prepare(server, name, rtype, TRANSPORT_UDP)
    if isinstance(prepared, DnsQueryResult):
        return prepared
    query_id, query = prepared
    started = time.perf_counter()
    deadline = _Deadline(timeout_s)
    sock = None
    try:
        sock = _open(socket.SOCK_DGRAM, server, cancel)
        sock.settimeout(deadline.remaining())
        sock.sendto(query, (server, port))
        while True:
            sock.settimeout(deadline.remaining())
            packet, _addr = sock.recvfrom(4096)
            try:
                message = parse_response(packet)
            except DnsWireError:
                continue
            # Чужой или опоздавший пакет: ждём свой, пока есть время.
            if message.query_id == (query_id & 0xFFFF) and message.is_response:
                return _answered(TRANSPORT_UDP, message, started)
    except ConnectionResetError as exc:
        # Так Windows сообщает, что на этом порту никто не слушает (ICMP «порт недоступен»).
        if cancel is not None and cancel.cancelled:
            return _failed(TRANSPORT_UDP, exc, cancel)
        return DnsQueryResult(
            status=STATUS_ERROR, transport=TRANSPORT_UDP, failure=FAILURE_REFUSED, detail=str(exc)
        )
    except (OSError, _Cancelled) as exc:
        return _failed(TRANSPORT_UDP, exc, cancel)
    finally:
        _close(sock, cancel)


def query_tcp(
    server: str,
    name: str,
    rtype: int,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    port: int = 53,
    cancel: SocketCancel | None = None,
) -> DnsQueryResult:
    """Один запрос по TCP (порт 53), без UDP.

    На Windows отказ «порт закрыт» приходит не сразу: система сама повторяет
    попытку соединения около двух секунд. При меньшем ``timeout_s`` закрытый
    порт выглядит как молчание.
    """
    prepared = _prepare(server, name, rtype, TRANSPORT_TCP)
    if isinstance(prepared, DnsQueryResult):
        return prepared
    query_id, query = prepared
    started = time.perf_counter()
    deadline = _Deadline(timeout_s)
    sock = None
    try:
        sock = _open(socket.SOCK_STREAM, server, cancel)
        sock.settimeout(deadline.remaining())
        sock.connect((server, port))
        return _answered(TRANSPORT_TCP, _exchange_framed(sock, query, query_id, deadline), started)
    except (OSError, DnsWireError, _Cancelled) as exc:
        return _failed(TRANSPORT_TCP, exc, cancel)
    finally:
        _close(sock, cancel)


_tls_context = None


def _client_tls_context():
    """Контекст с проверкой сертификата: один на процесс, корневые — из certifi."""
    global _tls_context
    if _tls_context is None:
        import ssl

        context = ssl.create_default_context()
        try:
            import certifi

            context.load_verify_locations(certifi.where())
        except Exception:
            pass
        _tls_context = context
    return _tls_context


def _connect_tls(
    server: str,
    port: int,
    tls_host: str,
    deadline: _Deadline,
    cancel: SocketCancel | None,
) -> tuple[socket.socket, socket.socket]:
    """(исходный сокет, сокет TLS). Сертификат проверяется для ``tls_host``."""
    raw = _open(socket.SOCK_STREAM, server, cancel)
    try:
        raw.settimeout(deadline.remaining())
        raw.connect((server, port))
        raw.settimeout(deadline.remaining())
        wrapped = _client_tls_context().wrap_socket(raw, server_hostname=tls_host)
    except BaseException:
        _close(raw, cancel)
        raise
    if cancel is not None and not cancel.track(wrapped):
        _close(wrapped, cancel)
        _close(raw, cancel)
        raise _Cancelled()
    return raw, wrapped


def query_dot(
    server: str,
    name: str,
    rtype: int,
    *,
    tls_host: str = "",
    timeout_s: float = ENCRYPTED_TIMEOUT_S,
    port: int = 853,
    cancel: SocketCancel | None = None,
) -> DnsQueryResult:
    """Шифрованный запрос DNS поверх TLS (RFC 7858), порт 853.

    Подключение идёт по адресу ``server``, а сертификат проверяется для имени
    ``tls_host`` (например, ``dns.google``). Без имени сертификат проверяется
    по самому адресу: так умеют только серверы, у которых адрес вписан в
    сертификат.
    """
    prepared = _prepare(server, name, rtype, TRANSPORT_DOT)
    if isinstance(prepared, DnsQueryResult):
        return prepared
    query_id, query = prepared
    started = time.perf_counter()
    deadline = _Deadline(timeout_s)
    raw = wrapped = None
    try:
        raw, wrapped = _connect_tls(server, port, tls_host or server, deadline, cancel)
        return _answered(TRANSPORT_DOT, _exchange_framed(wrapped, query, query_id, deadline), started)
    except (OSError, DnsWireError, _Cancelled) as exc:
        return _failed(TRANSPORT_DOT, exc, cancel)
    finally:
        _close(wrapped, cancel)
        _close(raw, cancel)


def query_doh(
    server: str,
    name: str,
    rtype: int,
    *,
    tls_host: str = "",
    port: int = 443,
    path: str = "/dns-query",
    timeout_s: float = ENCRYPTED_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> DnsQueryResult:
    """Шифрованный запрос DNS поверх HTTPS (RFC 8484).

    Подключение идёт прямо по адресу ``server``: системный DNS не участвует
    вовсе. Сертификат проверяется для ``tls_host``, а без него — по самому
    адресу. Такой ответ провайдер не может незаметно подменить.
    """
    import http.client

    prepared = _prepare(server, name, rtype, TRANSPORT_DOH)
    if isinstance(prepared, DnsQueryResult):
        return prepared
    query_id, query = prepared
    host = tls_host or server
    host_header = f"[{host}]" if ":" in host else host
    if port != 443:
        host_header = f"{host_header}:{port}"
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "Content-Type: application/dns-message\r\n"
        "Accept: application/dns-message\r\n"
        f"Content-Length: {len(query)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + query

    started = time.perf_counter()
    deadline = _Deadline(timeout_s)
    raw = wrapped = None
    try:
        raw, wrapped = _connect_tls(server, port, host, deadline, cancel)
        wrapped.settimeout(deadline.remaining())
        wrapped.sendall(request)
        wrapped.settimeout(deadline.remaining())
        response = http.client.HTTPResponse(wrapped, method="POST")
        response.begin()
        body = response.read(65535)
        if response.status != 200:
            return DnsQueryResult(
                status=STATUS_ERROR,
                transport=TRANSPORT_DOH,
                failure=FAILURE_HTTP,
                detail=f"HTTP {response.status}",
            )
        message = parse_response(body)
        if not message.is_response:
            raise DnsWireError("ответ не на наш запрос")
        return _answered(TRANSPORT_DOH, message, started)
    except http.client.HTTPException as exc:
        if cancel is not None and cancel.cancelled:
            return _failed(TRANSPORT_DOH, exc, cancel)
        return DnsQueryResult(
            status=STATUS_ERROR,
            transport=TRANSPORT_DOH,
            failure=FAILURE_MALFORMED,
            detail=str(exc) or type(exc).__name__,
        )
    except (OSError, DnsWireError, _Cancelled) as exc:
        return _failed(TRANSPORT_DOH, exc, cancel)
    finally:
        _close(wrapped, cancel)
        _close(raw, cancel)


def query_server(
    server: str,
    name: str,
    rtype: int,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    attempts: int = 2,
    port: int = 53,
    cancel: SocketCancel | None = None,
) -> DnsQueryResult:
    """Запрос так, как его делает обычная программа: UDP, а при усечённом ответе — TCP.

    Для проверки самих способов связи есть ``query_udp`` и ``query_tcp``: здесь
    важен ответ сервера, а не то, каким путём он пришёл.
    """
    last = DnsQueryResult(status=STATUS_TIMEOUT, transport=TRANSPORT_UDP, failure=FAILURE_TIMEOUT)
    for _attempt in range(max(1, int(attempts))):
        # Время считается от удачной попытки: повтор после молчания — не скорость сервера.
        result = query_udp(server, name, rtype, timeout_s=timeout_s, port=port, cancel=cancel)
        if result.answered and result.truncated:
            result = query_tcp(server, name, rtype, timeout_s=timeout_s, port=port, cancel=cancel)
        if result.answered or result.failure in (FAILURE_CANCELLED, FAILURE_OTHER):
            return result
        if result.status == STATUS_ERROR or last.status != STATUS_ERROR:
            last = result
    return last


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "ENCRYPTED_TIMEOUT_S",
    "FAILURE_CANCELLED",
    "FAILURE_CERT",
    "FAILURE_CLOSED",
    "FAILURE_HTTP",
    "FAILURE_MALFORMED",
    "FAILURE_OTHER",
    "FAILURE_REFUSED",
    "FAILURE_RESET",
    "FAILURE_TIMEOUT",
    "FAILURE_TLS",
    "FAILURE_UNREACHABLE",
    "RCODE_NXDOMAIN",
    "RCODE_OK",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_NXDOMAIN",
    "STATUS_OK",
    "STATUS_REFUSED",
    "STATUS_TIMEOUT",
    "TRANSPORT_DOH",
    "TRANSPORT_DOT",
    "TRANSPORT_TCP",
    "TRANSPORT_UDP",
    "TYPE_A",
    "TYPE_AAAA",
    "TYPE_CNAME",
    "TYPE_NS",
    "TYPE_PTR",
    "TYPE_TXT",
    "DnsMessage",
    "DnsQueryResult",
    "DnsRecord",
    "DnsWireError",
    "build_query",
    "encode_name",
    "failure_text",
    "parse_response",
    "query_doh",
    "query_dot",
    "query_server",
    "query_tcp",
    "query_udp",
    "reverse_name",
]
