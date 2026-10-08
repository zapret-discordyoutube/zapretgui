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

Расстояние до сервера
---------------------
Перебор идёт только по узлам ДО сервера. Пакет со сроком жизни, которого
хватает до самого сервера, доходит до него — и всё, что случится дальше, это
поведение сервера, а не фильтра на дороге. Поэтому сначала измеряется
расстояние (``tcp_distance``): соединение устанавливается только тогда, когда
срока жизни хватило до сервера; наименьший такой срок и есть расстояние. Не
измерили — место фильтра не ищем вовсе.

Второй способ, по TCP
---------------------
Для фильтра, который рвёт соединение сбросом. Соединяемся как обычно, затем
шлём приветствие с запрещённым именем со сроком жизни ``k``: до сервера оно
не дойдёт, а если фильтр ближе ``k`` — он пришлёт сброс. Тихий фильтр (просто
перестаёт пропускать) этим способом не находится: контроль это видит и вывода
не даёт.

Способу не нужны ни особые права, ни ответы промежуточных узлов (для обычных
соединений сетевой экран Windows их программам не отдаёт): наблюдаем только
за собственным соединением. Перед поиском делается контроль — без него вывод
не выдаётся: обычный пакет один должен получить ответ, а после запрещённого с
полным сроком жизни — не получить.
"""

from __future__ import annotations

import socket
import struct
import sys
import threading
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
    "FILTER_AT_TARGET",
    "FILTER_NO_DISTANCE",
    "FILTER_UNSURE",
    "FILTER_NOT_STATEFUL",
    "FILTER_NO_CONTROL",
    "FilterFacts",
    "FilterVerdict",
    "Hop",
    "RouteTrace",
    "judge_filter",
    "locate_filter",
    "send_pair",
    "tcp_distance",
    "tcp_pair",
    "trace_route",
]

MAX_HOPS = 30
HOP_ATTEMPTS = 2
HOP_TIMEOUT_MS = 1200
# Сколько ждать ответа сервера на обычный пакет.
REPLY_TIMEOUT_S = 1.5
# Пауза между запрещённым пакетом и обычным: фильтр должен успеть его увидеть.
SETTLE_S = 0.15
_FULL_TTL = 128

FILTER_FOUND = "found"
FILTER_NO_CONTROL = "no_control"
FILTER_NOT_STATEFUL = "not_stateful"
FILTER_NOT_ON_PATH = "not_on_path"
# Место вроде бы найдено, но контроль его не подтвердил.
FILTER_UNSURE = "unsure"
# Расстояние до сервера измерить не удалось: без него нельзя отличить фильтр от самого сервера.
FILTER_NO_DISTANCE = "no_distance"
# Поток гаснет, только когда пакет доходит до сервера: на дороге к нему фильтра не видно.
FILTER_AT_TARGET = "at_target"

# Сколько ждать соединения при измерении расстояния и сброса при поиске по TCP.
DISTANCE_TIMEOUT_S = 1.5
# Сколько соединений разом при измерении расстояния и на сколько узлов в обе
# стороны проверяется подсказка трассировки.
DISTANCE_AT_ONCE = 10
DISTANCE_AROUND = 3
# Сколько узлов трассировка спрашивает одновременно.
TRACE_AT_ONCE = 10
TCP_WINDOW_S = 1.2

# Имя, которое не блокируют: с ним тот же опыт должен проходить.
NEUTRAL_NAME = "example.com"


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
    """Все узлы до ``ip``. Идут по ``TRACE_AT_ONCE`` узлов разом, от ближних к дальним.

    Как только ответил сам адрес, узлы дальше него не спрашиваются: пинг с
    большим сроком жизни всё равно дошёл бы до того же адреса.
    """
    if ":" in ip:
        return RouteTrace(target=ip, supported=False)
    lock = threading.Lock()
    reached = [max_hops + 1]

    def one(ttl: int) -> Hop:
        result = TraceHopResult(HOP_SILENT)
        for _attempt in range(max(1, int(attempts))):
            if should_stop is not None and should_stop():
                break
            with lock:
                if ttl > reached[0]:
                    break
            result = probe(ip, ttl, timeout_ms=timeout_ms)
            if result.kind != HOP_SILENT:
                break
        if result.kind == HOP_TARGET:
            with lock:
                reached[0] = min(reached[0], ttl)
        return Hop(ttl=ttl, kind=result.kind, address=result.address, rtt_ms=result.rtt_ms)

    with ThreadPoolExecutor(max_workers=min(TRACE_AT_ONCE, max_hops), thread_name_prefix="trace") as pool:
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


def _connects_with(ip: str, ttl: int, port: int, timeout: float, token: SocketCancel) -> bool:
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return False
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, max(1, min(255, int(ttl))))
        sock.settimeout(timeout)
        sock.connect((ip, int(port)))
        return True
    except OSError:
        return False
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)


def _first_reaching(reached: dict[int, bool]) -> int | None:
    """Наименьший срок жизни, с которым соединились, если следом соединились ещё дважды.

    Дорога одна: если хватило ``first`` узлов, должно хватать и большего срока.
    Разрозненные попадания (балансировка по разным дорогам, потери) — не расстояние.
    """
    first = min((ttl for ttl, ok in reached.items() if ok), default=None)
    if first is None or not all(reached.get(ttl) for ttl in (first + 1, first + 2)):
        return None
    return first


def tcp_distance(
    ip: str,
    *,
    port: int = 443,
    max_hops: int = MAX_HOPS,
    timeout: float = DISTANCE_TIMEOUT_S,
    cancel: SocketCancel | None = None,
    around: int = 0,
    connects: Callable[..., bool] | None = None,
) -> int | None:
    """Сколько узлов до сервера: наименьший срок жизни, с которым соединение устанавливается.

    Меряется той же дорогой, какой пойдут пробы (TCP 443), и не зависит от того,
    отвечают ли узлы на пинг. None — соединиться не удалось ни с каким сроком
    жизни, расстояние неизвестно.

    ``around`` — подсказка от трассировки пингом: если она есть, сначала
    проверяются только сроки жизни рядом с ней (семь соединений вместо тридцати).
    Полный перебор идёт, только когда подсказки нет или она не подтвердилась, —
    и то по ``DISTANCE_AT_ONCE`` соединений разом: тридцать одновременных
    соединений к одному адресу сами выглядят для фильтра подозрительно.
    """
    if ":" in ip:
        return None
    token = cancel or SocketCancel()
    connect = connects or _connects_with
    reached: dict[int, bool] = {}

    def ask(ttls: list[int]) -> None:
        with ThreadPoolExecutor(max_workers=len(ttls), thread_name_prefix="distance") as pool:
            reached.update(zip(ttls, pool.map(lambda ttl: connect(ip, ttl, port, timeout, token), ttls)))

    if around:
        low = max(1, int(around) - DISTANCE_AROUND)
        ask(list(range(low, min(max_hops, int(around) + DISTANCE_AROUND) + 1)))
        if token.cancelled:
            return None
        first = _first_reaching(reached)
        # Годится, только если ниже найденного срока есть несоединившийся: иначе сервер может быть ещё ближе.
        if first is not None and (first == 1 or reached.get(first - 1) is False):
            return first
    for start in range(1, max_hops + 1, DISTANCE_AT_ONCE):
        wave = [ttl for ttl in range(start, min(max_hops, start + DISTANCE_AT_ONCE - 1) + 1) if ttl not in reached]
        if wave:
            ask(wave)
        if token.cancelled:
            return None
        first = min((ttl for ttl, ok in reached.items() if ok), default=None)
        # Дальше идти незачем: всё от первого узла до двух следующих за найденным уже проверено.
        if first is not None and all(ttl in reached for ttl in range(1, first + 3)):
            break
    return _first_reaching(reached)


def tcp_pair(
    ip: str,
    name: str,
    ttl: int | None,
    *,
    port: int = 443,
    window: float = TCP_WINDOW_S,
    cancel: SocketCancel | None = None,
) -> bool | None:
    """Проба по TCP. True — соединение живо, False — погашено, None — проверку сняли.

    ``ttl is None`` — контроль: приветствие с именем ``name`` обычным сроком
    жизни, «живо» значит, что сервер ответил. Иначе приветствие уходит со сроком
    жизни ``ttl``, и «погашено» значит, что за ``window`` секунд пришёл сброс:
    сервер такого пакета не видел, сбросить мог только фильтр на дороге.
    """
    from diagnostics.browser_hello import build_hello

    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if not token.track(sock):
            return None
        sock.settimeout(DISTANCE_TIMEOUT_S)
        sock.connect((ip, int(port)))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if ttl is not None:
            # Срок жизни остаётся низким всё окно: повторные отправки системы тоже не дойдут до сервера.
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, max(1, min(255, int(ttl))))
        sock.sendall(build_hello(name))
        sock.settimeout(window)
        try:
            answer = sock.recv(16)
        except (socket.timeout, TimeoutError):
            # Тишина: при контроле сервер не ответил, при пробе — сброса не было.
            return None if token.cancelled else ttl is not None
        # Данные — ответ сервера; пустое чтение — соединение закрыли.
        return bool(answer)
    except (ConnectionResetError, ConnectionAbortedError):
        return None if token.cancelled else False
    except OSError:
        return None if token.cancelled else False
    finally:
        if sock is not None:
            try:
                # Свой сброс должен дойти до сервера, иначе у него повиснет полуоткрытое соединение.
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, _FULL_TTL)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh" if sys.platform == "win32" else "ii", 1, 0))
            except OSError:
                pass
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
    # Контроль на найденном узле: с безобидным именем обычный пакет проходит.
    # False — поток пропадает и без запрещённого имени: дело не в фильтре. None — не проверяли.
    neutral_passes: bool | None = None
    # На следующем узле запрещённый пакет тоже гасит поток. False — не гасит:
    # находка была случайной потерей. None — не проверяли.
    next_blocked: bool | None = None
    # Сколько узлов до сервера. None — измерить не удалось. 0 — не измеряли (старые данные).
    distance: int | None = 0


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
    distance_of: Callable[..., int | None] = tcp_distance,
) -> FilterFacts:
    """Перебирает срок жизни запрещённого пакета, пока обычный не останется без ответа.

    Перебор идёт только по узлам до сервера (``distance_of``): пакет, которому
    хватило срока жизни до сервера, о фильтре на дороге ничего не говорит.
    """
    token = cancel or SocketCancel()
    distance = distance_of(ip, cancel=token)
    if token.cancelled:
        return FilterFacts(cancelled=True)
    if distance is None:
        return FilterFacts(distance=None)
    # Последний узел, на котором пакет ещё не у сервера.
    last = min(max(1, int(max_ttl)), distance - 1)

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
        return FilterFacts(cancelled=True, distance=distance)
    if not control:
        return FilterFacts(control_ok=False, distance=distance)
    stateful = silent_twice(_FULL_TTL)
    if stateful is None:
        return FilterFacts(control_ok=True, cancelled=True, distance=distance)
    if not stateful:
        return FilterFacts(control_ok=True, stateful=False, distance=distance)

    for ttl in range(1, last + 1):
        blocked = silent_twice(ttl)
        if blocked is None:
            return FilterFacts(control_ok=True, stateful=True, checked_up_to=ttl - 1, cancelled=True, distance=distance)
        if blocked:
            # Два контроля, прежде чем называть место: тот же срок жизни с безобидным
            # именем (поток должен выжить) и следующий узел (поток должен пропасть снова).
            neutral = pair(ip, NEUTRAL_NAME, ttl, cancel=token)
            # Следующий узел проверяем, только если он тоже ещё не сервер.
            beyond = (silent_twice(ttl + 1) if ttl + 1 <= last else True) if neutral else None
            return FilterFacts(
                control_ok=True,
                stateful=True,
                first_blocked_ttl=ttl,
                checked_up_to=ttl,
                cancelled=neutral is None or (bool(neutral) and beyond is None),
                neutral_passes=neutral,
                next_blocked=beyond,
                distance=distance,
            )
    return FilterFacts(control_ok=True, stateful=True, checked_up_to=last, distance=distance)


def _hop_name(trace: RouteTrace | None, ttl: int) -> str:
    hop = trace.hop(ttl) if trace is not None else None
    if hop is None or not hop.address:
        return f"узлом {ttl}"
    return f"узлом {ttl} ({hop.address})"


def judge_filter(facts: FilterFacts, trace: RouteTrace | None = None) -> FilterVerdict | None:
    """Вывод о месте фильтра или None, если проверку сняли."""
    if facts.cancelled:
        return None
    if facts.distance is None:
        return FilterVerdict(
            FILTER_NO_DISTANCE,
            "не удалось измерить, сколько узлов до сервера, — без этого фильтр на дороге не отличить от самого сервера",
        )
    if facts.distance == 1:
        return FilterVerdict(
            FILTER_NO_DISTANCE,
            "соединение с сервером устанавливается уже на первом узле: его принимает не сервер, а роутер, прокси, "
            "VPN или антивирус по дороге — искать место фильтра за ними нельзя",
        )
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
    # Сервер по трассе пингом может оказаться ближе, чем по TCP: берём меньшее.
    target_at = min(
        [value for value in (facts.distance, len(trace.hops) if trace is not None and trace.reached else 0) if value]
        or [0]
    )
    if hop is not None and target_at and hop >= target_at:
        hop = None
    if hop is None:
        if target_at and (facts.first_blocked_ttl is not None or facts.checked_up_to >= target_at - 1):
            return FilterVerdict(
                FILTER_AT_TARGET,
                f"на {max(0, target_at - 1)} узлах до сервера фильтр не найден: соединение гаснет, только когда "
                "пакет доходит до самого сервера или последнего участка перед ним",
            )
        return FilterVerdict(
            FILTER_NOT_ON_PATH,
            f"на первых {facts.checked_up_to} узлах фильтр не найден",
        )
    if facts.neutral_passes is False:
        return FilterVerdict(
            FILTER_UNSURE,
            f"на узле {hop} поток пропадает и с безобидным именем — так ведёт себя роутер, а не фильтр по имени; "
            "найти место фильтра этим способом здесь нельзя",
        )
    if facts.next_blocked is False:
        return FilterVerdict(
            FILTER_UNSURE,
            f"узел {hop} один раз погасил поток, но на следующем узле это не повторилось — "
            "похоже на случайную потерю пакетов, место фильтра не подтверждено",
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
