"""Проверка QUIC: доходит ли первый пакет с именем сайта до сервера.

QUIC — протокол поверх UDP (порт 443), на котором браузеры открывают YouTube
и многие другие сайты быстрее обычного. Блокируют его так же, как обычное
шифрованное соединение — по имени сайта в первом пакете, только пакет при
этом молча пропадает.

Способ тот же, что в ``diagnostics.block_cause``: на один и тот же адрес
уходит пакет с именем сайта, с посторонним именем и вовсе без имени.

* С именем сайта сервер ответил — QUIC работает.
* С именем сайта молчит, а с другим отвечает — QUIC блокируют по имени.
* Молчит во всех случаях — сервер QUIC не поддерживает или UDP 443 закрыт
  целиком. Это не блокировка сайта и проблемой не считается: браузер просто
  откроет сайт обычным соединением.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from utils.quic_initial import build_initial, is_reply_to
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "NEUTRAL_NAME",
    "QUIC_BLOCKED_BY_NAME",
    "QUIC_OK",
    "QUIC_SILENT",
    "QuicFacts",
    "QuicVerdict",
    "collect",
    "judge",
    "quic_hello",
]

NEUTRAL_NAME = "example.com"
QUIC_PORT = 443
# UDP теряется и сам по себе: одного молчания мало, чтобы назвать пакет заблокированным.
ATTEMPTS = 2
ATTEMPT_TIMEOUT_S = 1.5

QUIC_OK = "ok"
QUIC_BLOCKED_BY_NAME = "blocked_by_name"
QUIC_SILENT = "silent"


@dataclass(frozen=True, slots=True)
class QuicFacts:
    """Время ответа в миллисекундах на каждый из трёх пакетов; None — ответа не было."""

    host: str
    real_ms: float | None = None
    neutral_ms: float | None = None
    nameless_ms: float | None = None
    # Проверку сняли раньше, чем она закончилась.
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class QuicVerdict:
    code: str
    text: str


def quic_hello(
    ip: str,
    name: str | None,
    *,
    port: int = QUIC_PORT,
    attempts: int = ATTEMPTS,
    timeout: float = ATTEMPT_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> float | None:
    """Отправляет первый пакет QUIC с именем ``name`` и ждёт любой ответ сервера.

    Возвращает время до ответа в миллисекундах или None, если сервер молчит.
    """
    token = cancel or SocketCancel()
    for _attempt in range(max(1, int(attempts))):
        # Каждая попытка — новое соединение со своим номером: повтор старого
        # пакета сервер мог бы принять за дубль.
        packet = build_initial(name)
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_DGRAM)
            if not token.track(sock):
                return None
            started = time.perf_counter()
            deadline = started + timeout
            sock.sendto(packet.datagram, (ip, int(port)))
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                data, _address = sock.recvfrom(4096)
                if is_reply_to(data, packet):
                    return (time.perf_counter() - started) * 1000.0
        except OSError:
            # Тайм-аут, «порт недоступен», закрытый по отмене сокет: ответа нет.
            pass
        finally:
            if sock is not None:
                token.release(sock)
                close_quietly(sock)
        if token.cancelled:
            return None
    return None


def collect(host: str, ip: str, *, submit: Callable, cancel: SocketCancel) -> QuicFacts:
    """Три пакета на один адрес одновременно: с именем сайта, с посторонним и без имени."""
    real = submit(quic_hello, ip, host, cancel=cancel)
    neutral = submit(quic_hello, ip, NEUTRAL_NAME, cancel=cancel)
    nameless = submit(quic_hello, ip, None, cancel=cancel)
    return QuicFacts(
        host=host,
        real_ms=real.result(),
        neutral_ms=neutral.result(),
        nameless_ms=nameless.result(),
        cancelled=cancel.cancelled,
    )


def judge(facts: QuicFacts) -> QuicVerdict | None:
    """Вывод по трём пакетам или None, если проверку сняли."""
    if facts.real_ms is not None:
        return QuicVerdict(QUIC_OK, f"отвечает за {max(1, round(facts.real_ms))} мс")
    if facts.cancelled:
        return None
    if facts.neutral_ms is not None or facts.nameless_ms is not None:
        other = "с посторонним именем" if facts.neutral_ms is not None else "без имени"
        return QuicVerdict(
            QUIC_BLOCKED_BY_NAME,
            f"блокируется по имени сайта: пакет с именем {facts.host} пропадает, а {other} тот же сервер отвечает",
        )
    return QuicVerdict(
        QUIC_SILENT,
        "сервер не отвечает ни с каким именем — он не поддерживает QUIC или UDP 443 закрыт целиком",
    )
