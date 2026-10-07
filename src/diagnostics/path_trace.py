"""Путь до сервера по узлам и место, где стоит фильтр.

Трассировка
-----------
У пакета есть срок жизни — число узлов, которые он может пройти. Отправляя
пинг со сроком 1, 2, 3…, получаем по порядку адреса узлов, на которых он
истекал (``utils.windows_icmp.trace_hop_ipv4``). Видно, сколько узлов до
сервера и где ответы прекращаются.

Где стоит фильтр
----------------
Трассировка пингом показывает только, где пропадает пинг. Фильтр же обычно
пинг пропускает и режет лишь соединение с запрещённым сайтом. Его место
ищется иначе, через QUIC (UDP 443), и только если QUIC к этому сайту
блокируют по имени (см. ``diagnostics.quic_probe``).

Фильтр, увидев пакет с запрещённым именем, запоминает соединение и режет его
дальше целиком. Этим и пользуемся:

1. С одного и того же сокета уходит пакет с запрещённым именем и со сроком
   жизни ``k`` узлов — до сервера он заведомо не дойдёт.
2. Следом, с обычным сроком жизни, уходит пакет с посторонним именем.

Если фильтр стоит ближе ``k``-го узла, он видел первый пакет — и второй тоже
пропадёт. Если дальше — первого пакета он не видел, и сервер на второй
ответит. Наименьший ``k``, при котором ответа нет, и есть место фильтра: он
стоит между узлами ``k-1`` и ``k``.

Способу не нужны ни особые права, ни ответы промежуточных узлов (для обычных
соединений сетевой экран Windows их программам не отдаёт): наблюдаем только
за собственным соединением. Перед поиском делается контроль — без него вывод
не выдаётся: обычный пакет один должен получить ответ, а после запрещённого с
полным сроком жизни — не получить.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from utils.quic_initial import build_initial, is_reply_to
from utils.socket_cancel import SocketCancel, close_quietly
from utils.windows_icmp import HOP_ROUTER, HOP_SILENT, HOP_TARGET, HOP_UNSUPPORTED, TraceHopResult, trace_hop_ipv4

__all__ = [
    "FILTER_FOUND",
    "FILTER_NOT_ON_PATH",
    "FILTER_NOT_STATEFUL",
    "FILTER_NO_CONTROL",
    "FilterFacts",
    "FilterVerdict",
    "Hop",
    "RouteTrace",
    "judge_filter",
    "locate_filter",
    "send_pair",
    "trace_route",
]

MAX_HOPS = 30
HOP_ATTEMPTS = 2
HOP_TIMEOUT_MS = 1200
NEUTRAL_NAME = "example.com"
# Сколько ждать ответа сервера на обычный пакет.
REPLY_TIMEOUT_S = 1.5
# Пауза между запрещённым пакетом и обычным: фильтр должен успеть его увидеть.
SETTLE_S = 0.15
_FULL_TTL = 128

FILTER_FOUND = "found"
FILTER_NO_CONTROL = "no_control"
FILTER_NOT_STATEFUL = "not_stateful"
FILTER_NOT_ON_PATH = "not_on_path"


@dataclass(frozen=True, slots=True)
class Hop:
    ttl: int
    kind: str
    address: str = ""
    rtt_ms: float | None = None


@dataclass(frozen=True, slots=True)
class RouteTrace:
    target: str
    hops: tuple[Hop, ...] = ()
    # Дошли ли до самого адреса.
    reached: bool = False
    # False — трассировка в этой системе недоступна (не Windows или нет IPv4).
    supported: bool = True

    def hop(self, ttl: int) -> Hop | None:
        for item in self.hops:
            if item.ttl == ttl:
                return item
        return None


def trace_route(
    ip: str,
    *,
    max_hops: int = MAX_HOPS,
    attempts: int = HOP_ATTEMPTS,
    timeout_ms: int = HOP_TIMEOUT_MS,
    should_stop: Callable[[], bool] | None = None,
    probe: Callable[..., TraceHopResult] = trace_hop_ipv4,
) -> RouteTrace:
    """Все узлы до ``ip``. Сроки жизни проверяются одновременно, поэтому это быстро."""
    if ":" in ip:
        return RouteTrace(target=ip, supported=False)

    def one(ttl: int) -> Hop:
        result = TraceHopResult(HOP_SILENT)
        for _attempt in range(max(1, int(attempts))):
            if should_stop is not None and should_stop():
                break
            result = probe(ip, ttl, timeout_ms=timeout_ms)
            if result.kind != HOP_SILENT:
                break
        return Hop(ttl=ttl, kind=result.kind, address=result.address, rtt_ms=result.rtt_ms)

    with ThreadPoolExecutor(max_workers=max_hops, thread_name_prefix="trace") as pool:
        hops = list(pool.map(one, range(1, max_hops + 1)))
    if any(hop.kind == HOP_UNSUPPORTED for hop in hops):
        return RouteTrace(target=ip, supported=False)

    reached_at = next((hop.ttl for hop in hops if hop.kind == HOP_TARGET), None)
    if reached_at is not None:
        return RouteTrace(target=ip, hops=tuple(hops[:reached_at]), reached=True)
    # До адреса не дошли: хвост из одних молчащих узлов ничего не сообщает.
    last = max((hop.ttl for hop in hops if hop.kind == HOP_ROUTER), default=0)
    return RouteTrace(target=ip, hops=tuple(hops[:last]), reached=False)


# ---------------------------------------------------------------------------
# Место фильтра
# ---------------------------------------------------------------------------


def send_pair(
    ip: str,
    blocked_name: str,
    ttl: int | None,
    *,
    port: int = 443,
    reply_timeout: float = REPLY_TIMEOUT_S,
    settle: float = SETTLE_S,
    cancel: SocketCancel | None = None,
) -> bool | None:
    """Запрещённый пакет со сроком жизни ``ttl`` (None — не слать), затем обычный с того же сокета.

    Возвращает, ответил ли сервер на обычный пакет. None — проверку сняли.
    """
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if not token.track(sock):
            return None
        # Один сокет на оба пакета: фильтр узнаёт соединение по паре адресов и портов.
        sock.connect((ip, int(port)))
        if ttl is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, max(1, min(255, int(ttl))))
            sock.send(build_initial(blocked_name).datagram)
            time.sleep(settle)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, _FULL_TTL)
        neutral = build_initial(NEUTRAL_NAME)
        sock.send(neutral.datagram)
        deadline = time.perf_counter() + reply_timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None if token.cancelled else False
            sock.settimeout(remaining)
            if is_reply_to(sock.recv(4096), neutral):
                return True
    except OSError:
        return None if token.cancelled else False
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)


@dataclass(frozen=True, slots=True)
class FilterFacts:
    # Обычный пакет один получил ответ.
    control_ok: bool | None = None
    # После запрещённого пакета с полным сроком жизни обычный ответа не получил.
    stateful: bool | None = None
    # Наименьший срок жизни запрещённого пакета, при котором обычный остался без ответа.
    first_blocked_ttl: int | None = None
    # До какого срока жизни дошёл перебор.
    checked_up_to: int = 0
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class FilterVerdict:
    code: str
    text: str
    # Фильтр стоит между узлами ``hop - 1`` и ``hop``.
    hop: int | None = None


def locate_filter(
    ip: str,
    blocked_name: str,
    *,
    max_ttl: int = MAX_HOPS,
    cancel: SocketCancel | None = None,
    pair: Callable[..., bool | None] = send_pair,
) -> FilterFacts:
    """Перебирает срок жизни запрещённого пакета, пока обычный не останется без ответа."""
    token = cancel or SocketCancel()

    def ask(ttl: int | None) -> bool | None:
        return pair(ip, blocked_name, ttl, cancel=token)

    def silent_twice(ttl: int) -> bool | None:
        """Обычный пакет пропал дважды подряд: одна потеря — ещё не фильтр."""
        for _attempt in range(2):
            answered = ask(ttl)
            if answered is None:
                return None
            if answered:
                return False
        return True

    control = ask(None) or ask(None)
    if control is None:
        return FilterFacts(cancelled=True)
    if not control:
        return FilterFacts(control_ok=False)
    stateful = silent_twice(_FULL_TTL)
    if stateful is None:
        return FilterFacts(control_ok=True, cancelled=True)
    if not stateful:
        return FilterFacts(control_ok=True, stateful=False)

    for ttl in range(1, max(1, int(max_ttl)) + 1):
        blocked = silent_twice(ttl)
        if blocked is None:
            return FilterFacts(control_ok=True, stateful=True, checked_up_to=ttl - 1, cancelled=True)
        if blocked:
            return FilterFacts(control_ok=True, stateful=True, first_blocked_ttl=ttl, checked_up_to=ttl)
    return FilterFacts(control_ok=True, stateful=True, checked_up_to=max_ttl)


def _hop_name(trace: RouteTrace | None, ttl: int) -> str:
    hop = trace.hop(ttl) if trace is not None else None
    if hop is None or not hop.address:
        return f"узлом {ttl}"
    return f"узлом {ttl} ({hop.address})"


def judge_filter(facts: FilterFacts, trace: RouteTrace | None = None) -> FilterVerdict | None:
    """Вывод о месте фильтра или None, если проверку сняли."""
    if facts.cancelled:
        return None
    if not facts.control_ok:
        return FilterVerdict(
            FILTER_NO_CONTROL,
            "сервер не ответил даже на обычный пакет QUIC — искать место фильтра не на чем",
        )
    if not facts.stateful:
        return FilterVerdict(
            FILTER_NOT_STATEFUL,
            "после пакета с запрещённым именем обычный пакет всё равно проходит: фильтр не запоминает "
            "соединение, и найти его место этим способом нельзя",
        )
    hop = facts.first_blocked_ttl
    if hop is None:
        return FilterVerdict(
            FILTER_NOT_ON_PATH,
            f"на первых {facts.checked_up_to} узлах фильтр не найден",
        )
    if hop == 1:
        return FilterVerdict(
            FILTER_FOUND,
            f"фильтр стоит не дальше первого узла — это ваш роутер или программа на самом компьютере "
            f"(перед {_hop_name(trace, 1)})",
            hop,
        )
    return FilterVerdict(
        FILTER_FOUND,
        f"фильтр стоит между {_hop_name(trace, hop - 1)} и {_hop_name(trace, hop)}",
        hop,
    )
