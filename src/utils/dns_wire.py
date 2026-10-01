"""Запрос к конкретному DNS-серверу напрямую, без системного резолвера.

Windows умеет спрашивать только «свои» DNS-серверы, а для сравнения ответов
нужно спросить именно выбранный: 8.8.8.8, 1.1.1.1 и так далее. Поэтому запрос
собирается и разбирается вручную (формат RFC 1035) и уходит по UDP на порт 53;
если ответ не поместился в пакет (флаг TC), он повторяется по TCP.

Сторонняя библиотека dnspython здесь невозможна: её пакет называется ``dns`` и
перекрывается собственным ``src/dns``.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from dataclasses import dataclass
from ipaddress import ip_address

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

DEFAULT_TIMEOUT_S = 2.0
# Больше 512 байт по UDP сервер пришлёт, только если мы об этом попросили (EDNS0).
_EDNS_UDP_SIZE = 1232
_MAX_POINTER_HOPS = 32
_FLAG_QR = 0x8000
_FLAG_TC = 0x0200


class DnsWireError(ValueError):
    """Ответ сервера не разбирается как DNS-пакет."""


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


def _exchange_udp(server: str, port: int, query: bytes, query_id: int, timeout_s: float) -> DnsMessage | None:
    """Ответ по UDP или None, если сервер молчит."""
    deadline = time.perf_counter() + timeout_s
    with socket.socket(_family(server), socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_s)
        sock.sendto(query, (server, port))
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                packet, _addr = sock.recvfrom(4096)
            except socket.timeout:
                return None
            try:
                message = parse_response(packet)
            except DnsWireError:
                continue
            # Чужой или опоздавший пакет: ждём свой, пока есть время.
            if message.query_id == (query_id & 0xFFFF) and message.is_response:
                return message


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise DnsWireError("сервер закрыл соединение")
        data += chunk
    return data


def _exchange_tcp(server: str, port: int, query: bytes, timeout_s: float) -> DnsMessage:
    with socket.socket(_family(server), socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        sock.connect((server, port))
        sock.sendall(struct.pack("!H", len(query)) + query)
        size = struct.unpack("!H", _recv_exact(sock, 2))[0]
        return parse_response(_recv_exact(sock, size))


def _status_for(message: DnsMessage) -> str:
    if message.rcode == RCODE_NXDOMAIN:
        return STATUS_NXDOMAIN
    if message.rcode != RCODE_OK:
        return STATUS_REFUSED
    return STATUS_OK if message.answers else STATUS_EMPTY


def query_server(
    server: str,
    name: str,
    rtype: int,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    attempts: int = 2,
    port: int = 53,
) -> DnsQueryResult:
    """Спрашивает ``server`` про запись ``rtype`` имени ``name``. Исключений не бросает."""
    try:
        _family(server)
        build_query(0, name, rtype)
    except (ValueError, DnsWireError) as exc:
        return DnsQueryResult(status=STATUS_ERROR, detail=str(exc))

    detail = ""
    for _attempt in range(max(1, int(attempts))):
        # Время считаем от удачной попытки: повтор после молчания — не скорость сервера.
        started = time.perf_counter()
        query_id = int.from_bytes(os.urandom(2), "big")
        query = build_query(query_id, name, rtype)
        try:
            message = _exchange_udp(server, port, query, query_id, timeout_s)
            if message is not None and message.truncated:
                message = _exchange_tcp(server, port, query, timeout_s)
        except socket.timeout:
            message = None
        except (OSError, DnsWireError) as exc:
            # На Windows «порт закрыт» приходит как ConnectionResetError при чтении.
            detail = str(exc)
            continue
        if message is None:
            continue
        return DnsQueryResult(
            status=_status_for(message),
            rcode=message.rcode,
            records=message.answers,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail=f"rcode {message.rcode}" if message.rcode not in (RCODE_OK, RCODE_NXDOMAIN) else "",
        )
    if detail:
        return DnsQueryResult(status=STATUS_ERROR, detail=detail)
    return DnsQueryResult(status=STATUS_TIMEOUT)


_doh_context = None


def _doh_ssl_context():
    """Контекст с проверкой сертификата: один на процесс, корневые — из certifi."""
    global _doh_context
    if _doh_context is None:
        import ssl

        context = ssl.create_default_context()
        try:
            import certifi

            context.load_verify_locations(certifi.where())
        except Exception:
            pass
        _doh_context = context
    return _doh_context


def query_doh(
    server: str,
    name: str,
    rtype: int,
    *,
    path: str = "/dns-query",
    timeout_s: float = 4.0,
) -> DnsQueryResult:
    """Тот же запрос, но шифрованный (DNS поверх HTTPS, RFC 8484).

    Подключение идёт прямо по адресу сервера, без имени: так системный DNS не
    участвует вовсе, а сертификат сервера проверяется по его адресу. Такой
    ответ провайдер не может незаметно подменить — это эталон для сравнения.
    """
    import http.client
    import ssl

    try:
        _family(server)
        query = build_query(0, name, rtype)
    except (ValueError, DnsWireError) as exc:
        return DnsQueryResult(status=STATUS_ERROR, detail=str(exc))

    started = time.perf_counter()
    connection = http.client.HTTPSConnection(server, 443, timeout=timeout_s, context=_doh_ssl_context())
    try:
        connection.request(
            "POST",
            path,
            body=query,
            headers={"Content-Type": "application/dns-message", "Accept": "application/dns-message"},
        )
        response = connection.getresponse()
        body = response.read(65535)
        if response.status != 200:
            return DnsQueryResult(status=STATUS_ERROR, detail=f"HTTP {response.status}")
        message = parse_response(body)
    except socket.timeout:
        return DnsQueryResult(status=STATUS_TIMEOUT)
    except ssl.SSLError as exc:
        return DnsQueryResult(status=STATUS_ERROR, detail=f"TLS: {getattr(exc, 'reason', '') or exc}")
    except (OSError, http.client.HTTPException, DnsWireError) as exc:
        return DnsQueryResult(status=STATUS_ERROR, detail=str(exc) or type(exc).__name__)
    finally:
        connection.close()

    return DnsQueryResult(
        status=_status_for(message),
        rcode=message.rcode,
        records=message.answers,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "RCODE_NXDOMAIN",
    "RCODE_OK",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_NXDOMAIN",
    "STATUS_OK",
    "STATUS_REFUSED",
    "STATUS_TIMEOUT",
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
    "parse_response",
    "query_doh",
    "query_server",
    "reverse_name",
]
