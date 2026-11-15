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
    "QUIC_FLAKY",
    "QUIC_OK",
    "QUIC_SILENT",
    "QuicFacts",
    "QuicVerdict",
    "collect",
    "judge",
    "judge_again",
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
# Пакет с именем сайта пропал и на повторе, а позже, когда остальные проверки закончились, прошёл:
# фильтр (или линия) теряет его через раз — назвать это блокировкой по имени нельзя.
QUIC_FLAKY = "flaky"


@dataclass(frozen=True, slots=True)
class QuicFacts:
    """Время ответа в миллисекундах на каждый из трёх пакетов; None — ответа не было."""

    host: str
    real_ms: float | None = None
    neutral_ms: float | None = None
    nameless_ms: float | None = None
    # Настоящее имя ещё раз — только когда оно промолчало, а постороннее прошло:
    # одно молчание UDP ещё не сравнение. None — не ответило или не спрашивали.
    real_again_ms: float | None = None
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
    """Сначала один пакет с именем сайта — как у браузера. Только если сервер промолчал,
    уходят пакеты с посторонним именем и без имени: они нужны, чтобы назвать причину,
    а браузер таких не шлёт (``utils.probe_gate``: разбор — по сигналу беды)."""
    real_ms = quic_hello(ip, host, cancel=cancel)
    if real_ms is not None or cancel.cancelled:
        return QuicFacts(host=host, real_ms=real_ms, cancelled=cancel.cancelled)
    neutral = submit(quic_hello, ip, NEUTRAL_NAME, cancel=cancel)
    nameless = submit(quic_hello, ip, None, cancel=cancel)
    neutral_ms, nameless_ms = neutral.result(), nameless.result()
    real_again_ms = None
    if real_ms is None and (neutral_ms is not None or nameless_ms is not None) and not cancel.cancelled:
        real_again_ms = quic_hello(ip, host, cancel=cancel)
    return QuicFacts(
        host=host,
        real_ms=real_ms if real_ms is not None else real_again_ms,
        neutral_ms=neutral_ms,
        nameless_ms=nameless_ms,
        real_again_ms=real_again_ms,
        cancelled=cancel.cancelled,
    )


def judge_again(verdict: QuicVerdict | None, host: str, again_ms: float | None) -> QuicVerdict | None:
    """Итог повторного пакета с именем сайта, отправленного, когда остальные проверки закончились.

    Меняется только «по имени»: если на этот раз сервер ответил, пакет теряется через раз.
    """
    if verdict is None or verdict.code != QUIC_BLOCKED_BY_NAME or again_ms is None:
        return verdict
    return QuicVerdict(
        QUIC_FLAKY,
        f"то теряется, то проходит: пакет с именем {host} пропал и на повторе, а позже, когда остальные проверки "
        f"закончились, сервер ответил за {max(1, round(again_ms))} мс",
    )


def judge(facts: QuicFacts) -> QuicVerdict | None:
    """Вывод по пакетам или None, если проверку сняли.

    «По имени» говорится, только когда настоящее имя промолчало дважды
    (``collect`` повторяет его), а постороннее или пустое прошло.
    """
    if facts.real_ms is not None:
        return QuicVerdict(QUIC_OK, f"отвечает за {max(1, round(facts.real_ms))} мс")
    if facts.cancelled:
        return None
    if facts.neutral_ms is not None or facts.nameless_ms is not None:
        other = "с посторонним именем" if facts.neutral_ms is not None else "без имени"
        return QuicVerdict(
            QUIC_BLOCKED_BY_NAME,
            f"блокируется по имени сайта: пакет с именем {facts.host} пропадает и на повторе, а {other} тот же сервер отвечает",
        )
    return QuicVerdict(
        QUIC_SILENT,
        "сервер не отвечает ни с каким именем — он не поддерживает QUIC или UDP 443 закрыт целиком",
    )
