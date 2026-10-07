"""Обрыв после 16 КБ у конкретного сайта: набираем объём по одному соединению.

Общая проверка обрыва (``diagnostics.freeze_check``) качает файлы с нескольких
хостингов и отвечает на вопрос «режет ли провайдер зарубежные серверы вообще».
Про конкретный сайт она ничего не говорит, а основной запрос к сайту обрыв
видит, только если главная страница сама больше 16 КБ. Многие сайты отвечают
коротким перенаправлением — и обрыв остаётся незамеченным: «открывается», хотя
в браузере страница висит.

Фильтр считает данные **по соединению**, а не по запросу. Поэтому здесь по
одному соединению подряд уходит много запросов, пока от сервера не наберётся
больше 24 КБ. Набралось — обрыва нет. Соединение замерло в окне 16 КБ —
похоже на обрыв.

Чтобы не принять за обрыв сервер, который сам перестаёт отвечать на частые
одинаковые запросы, замирание проверяется второй раз другим видом запроса
(``HEAD``: ответы короче, и до того же объёма нужно больше запросов). Вывод
«обрыв» даётся, только если соединение замерло оба раза на том же объёме.

Сбор (``collect``) отделён от вывода (``judge``).
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass

from diagnostics.tls_probe import _client_context
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "CUT_MAX_BYTES",
    "CUT_MIN_BYTES",
    "TARGET_BYTES",
    "VOLUME_CUT",
    "VOLUME_OK",
    "VOLUME_UNKNOWN",
    "RUN_CANCELLED",
    "RUN_CLOSED",
    "RUN_FAILED",
    "RUN_PASSED",
    "RUN_RESET",
    "RUN_STALLED",
    "VolumeFacts",
    "VolumeRun",
    "VolumeVerdict",
    "collect",
    "download",
    "judge",
]

# Сколько набрать, чтобы уверенно сказать «обрыва нет».
TARGET_BYTES = 32 * 1024
# Окно, в котором замирание считается обрывом. Фильтр считает и служебные
# данные шифрования (сертификат сервера — несколько килобайт), а здесь
# считается только полезный ответ, поэтому нижняя граница ниже 16 КБ.
CUT_MIN_BYTES = 8_000
CUT_MAX_BYTES = 24_000
MAX_REQUESTS = 60
TIMEOUT_S = 5.0
# Столько ждём следующих данных: дольше — соединение замерло.
STALL_S = 3.0
# Больше этого одна попытка не длится, сколько бы запросов ни осталось.
BUDGET_S = 6.0
# После стольких ответов видно, наберётся ли объём: если сервер отвечает
# крошечными ответами и за все запросы не выйти даже за окно обрыва,
# попытка прекращается сразу.
ESTIMATE_AFTER = 6

RUN_PASSED = "passed"  # набрали нужный объём
RUN_STALLED = "stalled"  # данные перестали идти
RUN_RESET = "reset"  # соединение сброшено посреди работы
RUN_CLOSED = "closed"  # сервер сам закрыл соединение или объём не набрался
RUN_FAILED = "failed"  # не удалось даже начать
RUN_CANCELLED = "cancelled"

VOLUME_OK = "ok"
VOLUME_CUT = "cut"
VOLUME_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class VolumeRun:
    kind: str
    # Сколько байт ответа получено по соединению.
    received: int = 0
    # Сколько запросов успели отправить.
    requests: int = 0


@dataclass(frozen=True, slots=True)
class VolumeFacts:
    first: VolumeRun
    # None — второй раз не проверяли: первый не замирал.
    second: VolumeRun | None = None


@dataclass(frozen=True, slots=True)
class VolumeVerdict:
    code: str
    text: str
    # На скольких килобайтах замерло (для вывода «обрыв»).
    cut_kb: int = 0


class _Closed(Exception):
    """Сервер закрыл соединение."""


class _Enough(Exception):
    """Нужный объём набран: дочитывать ответ незачем."""


class _Reader:
    """Читает ответы подряд из одного соединения и считает полученное."""

    def __init__(self, sock, target: int) -> None:
        self._sock = sock
        self._target = target
        self._buffer = bytearray()
        self.total = 0

    def _fill(self) -> None:
        chunk = self._sock.recv(65536)
        if not chunk:
            raise _Closed
        self._buffer.extend(chunk)
        self.total += len(chunk)
        if self.total >= self._target:
            raise _Enough

    def until(self, marker: bytes, limit: int = 65536) -> bytes:
        while True:
            index = self._buffer.find(marker)
            if index >= 0:
                data = bytes(self._buffer[:index])
                del self._buffer[: index + len(marker)]
                return data
            if len(self._buffer) > limit:
                raise _Closed
            self._fill()

    def skip(self, count: int) -> None:
        while count > 0:
            if not self._buffer:
                self._fill()
            taken = min(count, len(self._buffer))
            del self._buffer[:taken]
            count -= taken

    def drain(self) -> None:
        """Тело без указанной длины: читаем, пока сервер не закроет соединение."""
        while True:
            self._buffer.clear()
            self._fill()


def _headers(head: bytes) -> tuple[int, dict[bytes, bytes]]:
    lines = head.split(b"\r\n")
    parts = lines[0].split(None, 2) if lines else []
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/") or not parts[1].isdigit():
        raise _Closed
    fields: dict[bytes, bytes] = {}
    for line in lines[1:]:
        name, _colon, value = line.partition(b":")
        fields[name.strip().lower()] = value.strip().lower()
    return int(parts[1]), fields


def _skip_body(reader: _Reader, method: str, status: int, fields: dict[bytes, bytes]) -> bool:
    """Дочитывает тело ответа. False — после него соединение закрыто, запросов больше не будет."""
    keep = fields.get(b"connection") != b"close"
    if method == "HEAD" or status < 200 or status in (204, 304):
        return keep
    if b"chunked" in fields.get(b"transfer-encoding", b""):
        while True:
            size = int(reader.until(b"\r\n").split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                # После последнего куска — необязательные поля и пустая строка.
                while reader.until(b"\r\n"):
                    pass
                return keep
            reader.skip(size + 2)
    length = fields.get(b"content-length")
    if length is not None and length.isdigit():
        reader.skip(int(length))
        return keep
    reader.drain()
    return False


def download(
    host: str,
    ip: str,
    path: str = "/",
    *,
    method: str = "GET",
    target: int = TARGET_BYTES,
    port: int = 443,
    timeout: float = TIMEOUT_S,
    stall: float = STALL_S,
    budget: float = BUDGET_S,
    cancel: SocketCancel | None = None,
) -> VolumeRun:
    """Шлёт запросы ``method`` по одному соединению с ``ip``, пока не наберётся ``target`` байт ответа."""
    token = cancel or SocketCancel()
    started = time.monotonic()
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    reader: _Reader | None = None
    requests = 0

    def _run(kind: str) -> VolumeRun:
        received = reader.total if reader is not None else 0
        if token.cancelled and kind != RUN_PASSED:
            kind = RUN_CANCELLED
        return VolumeRun(kind, received, requests)

    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return VolumeRun(RUN_CANCELLED)
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        wrapped = _client_context().wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
        token.track(wrapped)
        wrapped.settimeout(timeout)
        wrapped.do_handshake()
        reader = _Reader(wrapped, target)
        request = (
            f"{method} {path or '/'} HTTP/1.1\r\nHost: {host}\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
            "Accept: */*\r\nAccept-Encoding: identity\r\nConnection: keep-alive\r\n\r\n"
        ).encode("ascii", errors="ignore")
        wrapped.settimeout(stall)
        while requests < MAX_REQUESTS and time.monotonic() - started < budget:
            wrapped.sendall(request)
            requests += 1
            status, fields = _headers(reader.until(b"\r\n\r\n"))
            if not _skip_body(reader, method, status, fields):
                return _run(RUN_CLOSED)
            if requests >= ESTIMATE_AFTER and reader.total / requests * MAX_REQUESTS <= CUT_MAX_BYTES:
                return _run(RUN_CLOSED)
        return _run(RUN_CLOSED)
    except _Enough:
        return _run(RUN_PASSED)
    except (_Closed, ValueError):
        # Закрыл соединение сам или ответил так, что ответ не разобрать.
        return _run(RUN_CLOSED)
    except (socket.timeout, TimeoutError):
        return _run(RUN_FAILED if reader is None else RUN_STALLED)
    except (ssl.SSLError, OSError):
        # Сюда же попадает сброс соединения.
        return _run(RUN_FAILED if reader is None else RUN_RESET)
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token.release(item)
                close_quietly(item)


def _froze_in_window(run: VolumeRun) -> bool:
    return run.kind in (RUN_STALLED, RUN_RESET) and CUT_MIN_BYTES <= run.received <= CUT_MAX_BYTES


def collect(host: str, ip: str, path: str, *, cancel: SocketCancel) -> VolumeFacts:
    """Первая попытка обычными запросами; замерла в окне — вторая, запросами ``HEAD``."""
    first = download(host, ip, path, cancel=cancel)
    if not _froze_in_window(first) or cancel.cancelled:
        return VolumeFacts(first=first)
    return VolumeFacts(first=first, second=download(host, ip, path, method="HEAD", cancel=cancel))


def judge(facts: VolumeFacts) -> VolumeVerdict:
    first, second = facts.first, facts.second
    if RUN_CANCELLED in (first.kind, second.kind if second else ""):
        return VolumeVerdict(VOLUME_UNKNOWN, "проверку объёма прервали")
    # Нужный объём мог не набраться, но окно обрыва пройдено без замирания — обрыва нет.
    if first.kind == RUN_PASSED or (first.kind == RUN_CLOSED and first.received > CUT_MAX_BYTES):
        return VolumeVerdict(VOLUME_OK, f"по одному соединению получено {first.received // 1024} КБ без обрыва")
    if _froze_in_window(first):
        kb = first.received // 1024
        if second is not None and _froze_in_window(second):
            return VolumeVerdict(
                VOLUME_CUT,
                f"соединение замирает после {kb} КБ — дважды подряд, на {kb} и {second.received // 1024} КБ",
                cut_kb=kb,
            )
        if second is not None and second.kind == RUN_PASSED:
            return VolumeVerdict(
                VOLUME_UNKNOWN, f"один раз соединение замерло на {kb} КБ, при повторе прошло — случайный сбой"
            )
        return VolumeVerdict(
            VOLUME_UNKNOWN, f"соединение замерло на {kb} КБ, но повтор этого не подтвердил — вывода нет"
        )
    if first.kind == RUN_FAILED:
        return VolumeVerdict(VOLUME_UNKNOWN, "не удалось соединиться повторно — объём не проверен")
    if first.kind in (RUN_STALLED, RUN_RESET):
        return VolumeVerdict(
            VOLUME_UNKNOWN,
            f"соединение прервалось на {first.received // 1024} КБ — это не похоже на обрыв после 16 КБ",
        )
    return VolumeVerdict(
        VOLUME_UNKNOWN,
        f"по одному соединению удалось набрать только {first.received // 1024} КБ — "
        "на таком объёме обрыв после 16 КБ не проверить",
    )
