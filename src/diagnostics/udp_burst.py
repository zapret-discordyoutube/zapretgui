"""UDP по числу пакетов: не замирает ли поток после первых двух десятков.

Обычная проверка звонков шлёт серверу один запрос и по ответу говорит «UDP
проходит». Но фильтр умеет пропускать начало потока и глушить его дальше —
примерно после 25 пакетов (так же, как обрыв «после 16 КБ» у TCP). Тогда
звонок или игра соединяются и через пару секунд замирают, а одиночный запрос
этого не видит.

Здесь с одного сокета подряд уходит тридцать запросов STUN, и смотрим, на
какие пришёл ответ. Заморозка выглядит так: первые ответы идут, потом —
тишина до конца.

Осторожность. Так же выглядит сервер, который сам ограничивает частоту
запросов. Поэтому вывод делается только при совпадении: два разных сервера
у разных владельцев, каждый проверен дважды с разных портов, и ответы
обрываются примерно на одном и том же пакете. Иначе — «не похоже» или
«узнать не удалось».

Здесь только отправка серии и чистый вывод.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

from blockcheck.stun_tester import build_stun_request
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = ["BURST_OK", "BURST_FREEZE", "BURST_UNKNOWN", "BurstVerdict", "check_bursts", "judge", "lines", "send_burst"]

SERVERS = (("Google", "stun.l.google.com", 19302), ("Cloudflare", "stun.cloudflare.com", 3478))
PACKETS = 30
GAP_S = 0.02
TAIL_WAIT_S = 1.0
REPEATS = 2
# Сколько первых ответов должно прийти, чтобы говорить «начало потока проходит».
HEAD_MIN = 10
# Сколько последних пакетов должно остаться без ответа, чтобы говорить «дальше тишина».
TAIL_MIN = 4
# На сколько пакетов может расходиться место обрыва между сериями.
SPREAD = 4

BURST_OK = "ok"
BURST_FREEZE = "freeze"
BURST_UNKNOWN = "unknown"


def send_burst(
    host: str,
    port: int,
    *,
    packets: int = PACKETS,
    gap: float = GAP_S,
    tail_wait: float = TAIL_WAIT_S,
    cancel: SocketCancel | None = None,
) -> tuple[bool, ...] | None:
    """Серия запросов с одного сокета: на какие по счёту пришёл ответ. None — сервер недоступен или проверку сняли."""
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    try:
        address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if not token.track(sock):
            return None
        sock.connect(address)
        sent: dict[bytes, int] = {}
        answered = [False] * packets

        def drain(until: float) -> None:
            while True:
                remaining = until - time.monotonic()
                if remaining <= 0:
                    return
                sock.settimeout(remaining)
                try:
                    data = sock.recv(2048)
                except (socket.timeout, TimeoutError):
                    return
                except OSError:
                    # «Порт недоступен» от сети приходит ошибкой чтения: это просто отсутствие ответа.
                    return
                index = sent.get(bytes(data[8:20]))
                if index is not None:
                    answered[index] = True

        for index in range(packets):
            request = build_stun_request()
            sent[bytes(request[8:20])] = index
            try:
                sock.send(request)
            except OSError:
                pass
            drain(time.monotonic() + gap)
        drain(time.monotonic() + tail_wait)
        return None if token.cancelled else tuple(answered)
    except OSError:
        return None
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)


def _cut_at(answered: tuple[bool, ...]) -> int | None:
    """Номер пакета, после которого ответы прекратились. None — серия не похожа на обрыв."""
    if not answered or True not in answered:
        return None
    last = len(answered) - 1 - answered[::-1].index(True)
    head = sum(answered[: last + 1])
    tail = len(answered) - 1 - last
    return last + 1 if head >= HEAD_MIN and tail >= TAIL_MIN else None


@dataclass(frozen=True, slots=True)
class BurstVerdict:
    code: str
    text: str
    # (сервер, серии: сколько ответов из скольких).
    servers: tuple[tuple[str, tuple[str, ...]], ...] = ()


def check_bursts(send: Callable[[str, int], tuple[bool, ...] | None], *, submit: Callable) -> tuple[tuple[str, tuple], ...]:
    """Серверы — одновременно, серии к одному серверу — по очереди (каждая с нового сокета, то есть с другого порта)."""

    def one(host: str, port: int) -> tuple:
        return tuple(send(host, port) for _attempt in range(REPEATS))

    futures = [(name, submit(one, host, port)) for name, host, port in SERVERS]
    return tuple((name, future.result()) for name, future in futures)


def lines(verdict: BurstVerdict | None) -> list[str]:
    """Строки для текстового отчёта. Пусто — вывода нет."""
    if verdict is None:
        return []
    icon = {BURST_OK: "✅", BURST_FREEZE: "⚠️"}.get(verdict.code, "❔")
    return [f"{icon} Серия пакетов UDP: {verdict.text}"] + [
        f"   {name}: ответов {', '.join(series)}" for name, series in verdict.servers
    ]


def judge(facts) -> BurstVerdict | None:
    """Вывод по сериям. None — ни один сервер не ответил вовсе: об этом скажет обычная проверка звонков."""
    shown = tuple(
        (name, tuple("нет ответа" if run is None else f"{sum(run)} из {len(run)}" for run in runs)) for name, runs in facts
    )
    runs = [run for _name, series in facts for run in series if run is not None and any(run)]
    if not runs:
        return None
    full = [run for run in runs if sum(run) >= len(run) - 2]
    if len(full) == len(runs):
        return BurstVerdict(BURST_OK, f"серия из {PACKETS} пакетов UDP проходит целиком — заморозки по числу пакетов нет", shown)
    cuts_by_server = []
    for _name, series in facts:
        cuts = [_cut_at(run) for run in series if run is not None]
        if len(cuts) == REPEATS and all(cut is not None for cut in cuts):
            cuts_by_server.append(cuts)
    cuts = [cut for item in cuts_by_server for cut in item]
    if len(cuts_by_server) == len(SERVERS) and max(cuts) - min(cuts) <= SPREAD:
        middle = sorted(cuts)[len(cuts) // 2]
        return BurstVerdict(
            BURST_FREEZE,
            f"ответы по UDP прекращаются примерно после {middle} пакетов — одинаково на двух разных серверах и при "
            "повторе с другого порта. Похоже на заморозку UDP по числу пакетов: звонки и игры могут соединяться "
            "и замирать через несколько секунд",
            shown,
        )
    return BurstVerdict(
        BURST_UNKNOWN,
        "часть пакетов серии осталась без ответа, но одинаковой картины на двух серверах нет — "
        "это похоже на потери или ограничение самого сервера, а не на фильтр",
        shown,
    )
