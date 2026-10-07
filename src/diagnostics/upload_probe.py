"""Обрыв при отправке данных: замирает ли соединение, когда данные идут от нас.

Проверка загрузки (``diagnostics.freeze_check``) ловит обрыв, когда данные
идут к нам. Ограничение работает и в обратную сторону: соединение замирает,
когда много отправляем мы. По описанию проекта dpi-checkers считается оно не
в байтах, а в пакетах — около двадцати пяти в обе стороны.

Чтобы одиночный тайм-аут не выглядел блокировкой, проверка идёт с контролем
на том же сервере:

1. **Короткая отправка** — 16 байт. Сервер обязан ответить хоть чем-то
   (пусть отказом): иначе он просто не принимает такие запросы, и судить не
   по чему.
2. **Большая отправка** — 64 КБ одним потоком.
3. **Отправка мелкими пакетами** — 64 байта, по два байта с паузой: данных
   почти нет, а пакетов много.

Короткая прошла, а большая замерла — режут по объёму. Большая прошла, а
мелкие пакеты замерли — режут по числу пакетов. Сброс соединения сервером на
большой отправке выводом не считается: так сервер может сам отказаться от
лишних данных.
"""

from __future__ import annotations

import os
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from diagnostics.tls_probe import _client_context
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "POST_ANSWERED",
    "POST_CANCELLED",
    "POST_EARLY",
    "POST_FAILED",
    "POST_STALLED",
    "UPLOAD_BULK_STALLS",
    "UPLOAD_OK",
    "UPLOAD_PACKET_LIMIT",
    "UPLOAD_UNKNOWN",
    "PostResult",
    "UploadFacts",
    "UploadVerdict",
    "collect",
    "judge",
    "post",
]

SMALL_BYTES = 16
BULK_BYTES = 64 * 1024
BULK_CHUNK = 4096
DRIP_BYTES = 64
DRIP_CHUNK = 2
DRIP_PAUSE_S = 0.05
TIMEOUT_S = 5.0
# Сколько ждать ответа до отправки тела: успел ответить — значит, тела не ждёт.
EARLY_ANSWER_WAIT_S = 0.6

POST_ANSWERED = "answered"  # сервер прислал ответ HTTP — любой
POST_EARLY = "early"  # сервер ответил, не дождавшись тела: наши данные он не читал
POST_STALLED = "stalled"  # соединение замерло: ни отправить, ни получить
POST_FAILED = "failed"  # сброс, отказ, ошибка шифрования
POST_CANCELLED = "cancelled"

UPLOAD_OK = "ok"
UPLOAD_BULK_STALLS = "bulk_stalls"
UPLOAD_PACKET_LIMIT = "packet_limit"
UPLOAD_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PostResult:
    kind: str
    # Сколько байт тела успели отдать системе до ответа или сбоя.
    sent: int = 0
    status: int | None = None


@dataclass(frozen=True, slots=True)
class UploadFacts:
    small: PostResult
    # None — не отправляли: короткая отправка не прошла, сравнивать не с чем.
    bulk: PostResult | None = None
    drip: PostResult | None = None


@dataclass(frozen=True, slots=True)
class UploadVerdict:
    code: str
    text: str


def post(
    host: str,
    ip: str,
    path: str,
    chunks: Iterable[bytes],
    *,
    pause: float = 0.0,
    port: int = 443,
    timeout: float = TIMEOUT_S,
    early_wait: float = 0.0,
    cancel: SocketCancel | None = None,
) -> PostResult:
    """``POST https://host/path`` по адресу ``ip``; тело уходит кусками ``chunks`` с паузой ``pause``.

    ``timeout`` — сколько ждать на каждом шаге: соединение, отправка куска, ответ.
    ``early_wait`` — сколько подождать ответа после заголовков, до тела. Сервер,
    который отвечает сразу (например, отказом), тело не читает: то, что ответ
    пришёл, ничего не скажет о том, прошли ли наши данные.
    """
    token = cancel or SocketCancel()
    chunks = tuple(chunks)
    total = sum(len(chunk) for chunk in chunks)
    sock: socket.socket | None = None
    wrapped: ssl.SSLSocket | None = None
    sent = 0

    def _result(kind: str, status: int | None = None) -> PostResult:
        return PostResult(POST_CANCELLED if token.cancelled and kind != POST_ANSWERED else kind, sent, status)

    try:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return PostResult(POST_CANCELLED)
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        # Мелкие куски должны уйти отдельными пакетами, а не склеиться в один.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        wrapped = _client_context().wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
        token.track(wrapped)
        wrapped.settimeout(timeout)
        wrapped.do_handshake()

        head = (
            f"POST {path or '/'} HTTP/1.1\r\nHost: {host}\r\n"
            "User-Agent: Mozilla/5.0\r\nAccept: */*\r\n"
            "Content-Type: application/octet-stream\r\n"
            f"Content-Length: {total}\r\nConnection: close\r\n\r\n"
        )
        wrapped.sendall(head.encode("ascii", errors="ignore"))
        if early_wait > 0:
            wrapped.settimeout(early_wait)
            try:
                early = wrapped.recv(64)
            except (socket.timeout, TimeoutError):
                early = None
            if early is not None:
                return _result(POST_EARLY if early else POST_FAILED, _status(early))
        for index, chunk in enumerate(chunks):
            if index and pause:
                time.sleep(pause)
            wrapped.settimeout(timeout)
            wrapped.sendall(chunk)
            sent += len(chunk)

        wrapped.settimeout(timeout)
        status = _status(wrapped.recv(64))
        return _result(POST_ANSWERED if status is not None else POST_FAILED, status)
    except (socket.timeout, TimeoutError):
        return _result(POST_STALLED)
    except OSError:
        # Сюда же попадают сброс соединения и ошибки шифрования.
        return _result(POST_FAILED)
    finally:
        for item in (wrapped, sock):
            if item is not None:
                token.release(item)
                close_quietly(item)


def _status(answer: bytes) -> int | None:
    parts = answer.split(None, 2)
    if answer.startswith(b"HTTP/") and len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return None


def _split(data: bytes, size: int) -> list[bytes]:
    return [data[offset : offset + size] for offset in range(0, len(data), size)]


def collect(host: str, ip: str, path: str, *, submit: Callable, cancel: SocketCancel) -> UploadFacts:
    """Короткая отправка, а если она прошла — большая и мелкими пакетами одновременно."""
    # Контроль: сервер должен дождаться тела и только потом ответить.
    small = post(host, ip, path, [os.urandom(SMALL_BYTES)], early_wait=EARLY_ANSWER_WAIT_S, cancel=cancel)
    if small.kind != POST_ANSWERED:
        return UploadFacts(small=small)
    bulk = submit(post, host, ip, path, _split(os.urandom(BULK_BYTES), BULK_CHUNK), cancel=cancel)
    drip = submit(post, host, ip, path, _split(os.urandom(DRIP_BYTES), DRIP_CHUNK), pause=DRIP_PAUSE_S, cancel=cancel)
    return UploadFacts(small=small, bulk=bulk.result(), drip=drip.result())


def judge(facts: UploadFacts) -> UploadVerdict:
    if facts.small.kind == POST_CANCELLED or POST_CANCELLED in (
        facts.bulk.kind if facts.bulk else "",
        facts.drip.kind if facts.drip else "",
    ):
        return UploadVerdict(UPLOAD_UNKNOWN, "проверку отправки прервали")
    if facts.small.kind == POST_EARLY:
        return UploadVerdict(
            UPLOAD_UNKNOWN, "сервер отвечает, не дочитав отправленное, — проверить отправку на нём нельзя"
        )
    if facts.small.kind != POST_ANSWERED or facts.bulk is None or facts.drip is None:
        return UploadVerdict(UPLOAD_UNKNOWN, "сервер не принимает отправку данных — проверить её не на чем")
    if facts.bulk.kind == POST_STALLED:
        return UploadVerdict(
            UPLOAD_BULK_STALLS,
            f"отправка {BULK_BYTES // 1024} КБ замирает, хотя короткую отправку тот же сервер принимает",
        )
    if facts.drip.kind == POST_STALLED:
        packets = DRIP_BYTES // DRIP_CHUNK
        return UploadVerdict(
            UPLOAD_PACKET_LIMIT,
            f"соединение замирает на {packets} мелких пакетах, хотя большой объём проходит — "
            "режут по числу пакетов, а не по объёму",
        )
    if facts.bulk.kind != POST_ANSWERED or facts.drip.kind != POST_ANSWERED:
        # Сброс вместо ответа: сервер мог сам отказаться от лишних данных.
        return UploadVerdict(UPLOAD_UNKNOWN, "сервер оборвал отправку сам — это не похоже на замирание")
    return UploadVerdict(UPLOAD_OK, "отправка данных проходит без обрыва")
