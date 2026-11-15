"""Все видеосерверы YouTube, которые нашла проверка: каждый — по IPv4 и по IPv6.

YouTube сам выбирает узел для ролика, а фильтр режет узлы поодиночке: один открывается, другой
получает ваше приветствие и молчит, у третьего ответ приходит только при малом окне приёма. Основная
проверка (``engine._probe_target``) разбирает один узел подробно. Здесь — быстрый обход всех найденных:
видно, сколько узлов открывается и чем закрыты остальные, а не только один.

На узле проверяется только приветствие (ответ сервера дошёл или нет), без скачивания видео: адрес
замера скорости с подписью годится только для своего узла (``video_nodes.goodput_volume``).

Сбор (``collect``) отделён от вывода (``judge``): вывод — чистая функция над фактами. Слова для
экрана и отчёта задаёт только ``service_view.video_sweep_rows``.
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass

from diagnostics import block_cause
from diagnostics.block_cause import HelloResult

__all__ = [
    "FAMILY_V4",
    "FAMILY_V6",
    "FAR_RTT_MS",
    "SWEEP_CUT_WINDOW",
    "SWEEP_FLAKY",
    "SWEEP_NO_ROUTE",
    "SWEEP_OK",
    "SWEEP_RESET",
    "SWEEP_SILENT",
    "SWEEP_UNCLEAR",
    "SweepFacts",
    "SweepNode",
    "collect",
    "judge",
    "judge_node",
    "node_group",
    "sweep_hosts",
]

FAMILY_V4 = "IPv4"
FAMILY_V6 = "IPv6"

# Сервер ответил на приветствие.
SWEEP_OK = "ok"
# Соединение есть, ответ молчит, но с окном приёма в 1 байт приходит: режут ответ, обходит ``wssize``.
SWEEP_CUT_WINDOW = "cut_window"
# Соединение есть, ответа нет ни так, ни так.
SWEEP_SILENT = "silent"
# Соединение сбрасывают.
SWEEP_RESET = "reset"
# Соединение TCP не устанавливается.
SWEEP_NO_ROUTE = "no_route"
# Ответ не удалось истолковать.
SWEEP_UNCLEAR = "unclear"
# Ответ приходит не на каждую попытку: фильтр (или балансировка у провайдера) пропускает через раз.
SWEEP_FLAKY = "flaky"

# Сколько узлов обходить за один раз и сколько ждать ответ: обход идёт после основной проверки.
SWEEP_MAX_NODES = 4
SWEEP_TIMEOUT_S = 4.0
# Попыток приветствия на адрес. По форуму (ноябрь 2026) узлы открываются «то с первой попытки, то сто раз
# подряд нет»: одна попытка давала бы подброшенную монету вместо вывода.
SWEEP_TRIES = 5
# Если столько первых попыток подряд не прошли, остальные не делаются: каждая ждёт таймаут.
SWEEP_GIVE_UP = 3
SWEEP_RETRY_TIMEOUT_S = 3.0
# Сколько кэшей, закрытых кнопкой «Закрыть кэши YouTube», проверять по настоящему адресу (мимо hosts).
SWEEP_MAX_CLOSED = 2
# Круг до узла (соединение TCP), с которого узел «далеко»: кэш у провайдера — единицы и десятки мс,
# Европа — 30–70 мс, США и Азия — от 120. Видео с дальнего узла грузится медленно и без блокировки.
FAR_RTT_MS = 120.0

# Имя видеосервера: ``rr4---sn-xguxaxjvh-304e.googlevideo.com`` (или старое ``r2---…``, или ``….c.youtube.com``).
# Группа узла — ``sn-xguxaxjvh-304e``: одна площадка, на ней машины rr1, rr2, … с тем же хвостом.
_NODE_NAME = re.compile(r"^r{1,2}\d+---(sn-[a-z0-9-]+)\.(?:googlevideo\.com|c\.youtube\.com)$", re.IGNORECASE)


def node_group(host: str) -> str:
    """Группа узла по имени видеосервера. Пусто — имя не похоже на видеосервер."""
    found = _NODE_NAME.match(str(host or "").strip().rstrip("."))
    return found.group(1).lower() if found else ""


@dataclass(frozen=True, slots=True)
class SweepFacts:
    host: str
    ip: str
    family: str
    # Обычное приветствие с именем узла.
    hello: HelloResult
    # Приветствие с окном приёма в 1 байт. None — не пробовалось (обычное приветствие дошло или соединения нет).
    small: HelloResult | None = None
    # Владелец сети адреса (``utils.ip_owner.IpOwner``). None — узнать не удалось.
    owner: object | None = None
    # Повторные попытки того же приветствия (после ``hello``).
    more: tuple[HelloResult, ...] = ()
    # Группа узла закрыта в hosts кнопкой «Закрыть кэши YouTube»; адрес взят из DNS мимо hosts.
    closed: bool = False

    @property
    def attempts(self) -> tuple[HelloResult, ...]:
        return (self.hello, *self.more)

    @property
    def rtt_ms(self) -> float | None:
        """Самое быстрое соединение TCP из попыток: один круг до узла и обратно."""
        times = [item.connect_ms for item in self.attempts if item.connect_ms is not None]
        return min(times) if times else None


@dataclass(frozen=True, slots=True)
class SweepNode:
    host: str
    ip: str
    family: str
    code: str
    # Группа узла (``node_group``).
    group: str = ""
    # Узел в сети провайдера (кэш Google у провайдера), а не в сети самого Google. False — Google или неизвестно.
    provider: bool = False
    # Чья сеть словами (``video_nodes.owner_text``). Пусто — владельца узнать не удалось.
    owner: str = ""
    # Номер сети (AS) владельца: по номерам сетей кэшей журнал исправлений узнаёт, та же ли это сеть.
    asn: str = ""
    # Сколько попыток приветствия прошло из сделанных.
    passes: int = 0
    tries: int = 0
    # Круг до узла по соединению TCP, мс. None — соединения не было.
    rtt_ms: float | None = None
    # Узел из группы, закрытой в hosts; проверен по настоящему адресу.
    closed: bool = False

    def as_dict(self) -> dict:
        return {
            "host": self.host,
            "ip": self.ip,
            "family": self.family,
            "code": self.code,
            "group": self.group,
            "provider": self.provider,
            "owner": self.owner,
            "asn": self.asn,
            "passes": self.passes,
            "tries": self.tries,
            "rtt_ms": None if self.rtt_ms is None else round(self.rtt_ms),
            "closed": self.closed,
        }


def judge_node(facts: SweepFacts) -> str:
    """Код одного узла по попыткам приветствия и приветствию с малым окном."""
    passes = sum(1 for item in facts.attempts if item.answered)
    if passes == len(facts.attempts):
        return SWEEP_OK
    if passes:
        return SWEEP_FLAKY
    kind = facts.hello.kind
    if kind == block_cause.HELLO_CONNECT:
        return SWEEP_NO_ROUTE
    if kind in (block_cause.HELLO_TIMEOUT, block_cause.HELLO_RESET):
        if facts.small is not None and facts.small.answered:
            return SWEEP_CUT_WINDOW
        return SWEEP_SILENT if kind == block_cause.HELLO_TIMEOUT else SWEEP_RESET
    return SWEEP_UNCLEAR


def judge(facts: tuple[SweepFacts, ...]) -> tuple[SweepNode, ...]:
    """Узлы с кодами. Если ни один адрес IPv6 не соединился, IPv6 отбрасывается целиком:
    нет дороги по IPv6 чаще означает, что IPv6 у вас не настроен, а не что узел закрыт."""
    from diagnostics import video_nodes

    nodes = tuple(
        SweepNode(
            item.host,
            item.ip,
            item.family,
            judge_node(item),
            group=node_group(item.host),
            provider=item.owner is not None and bool(getattr(item.owner, "asn", "")) and not video_nodes.is_google(item.owner),
            owner=video_nodes.owner_text(item.owner),
            asn=str(getattr(item.owner, "asn", "") or ""),
            passes=sum(1 for attempt in item.attempts if attempt.answered),
            tries=len(item.attempts),
            rtt_ms=item.rtt_ms,
            closed=item.closed,
        )
        for item in facts
    )
    v6 = [node for node in nodes if node.family == FAMILY_V6]
    if v6 and all(node.code == SWEEP_NO_ROUTE for node in v6):
        return tuple(node for node in nodes if node.family != FAMILY_V6)
    return nodes


def _ipv6_address(host: str) -> str:
    """Первый адрес IPv6 узла по DNS системы. Пусто — адреса нет или DNS не ответил."""
    try:
        found = socket.getaddrinfo(host, 443, socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return ""
    return str(found[0][4][0]) if found else ""


def _attempts(run, host: str, ip: str) -> list[HelloResult]:
    """До ``SWEEP_TRIES`` приветствий подряд. Без соединения повторять нечего; три неудачи подряд с начала — хватит."""
    attempts = [block_cause.tls_hello(ip, host, cancel=run.probe_cancel, timeout=SWEEP_TIMEOUT_S)]
    while len(attempts) < SWEEP_TRIES and not run.dns_cancelled():
        if attempts[-1].kind in (block_cause.HELLO_CONNECT, block_cause.HELLO_CANCELLED):
            break
        if len(attempts) >= SWEEP_GIVE_UP and not any(item.answered for item in attempts):
            break
        attempts.append(block_cause.tls_hello(ip, host, cancel=run.probe_cancel, timeout=SWEEP_RETRY_TIMEOUT_S))
    return attempts


def _one(run, host: str, ip: str, family: str, closed: bool = False) -> SweepFacts:
    from diagnostics import video_nodes

    attempts = _attempts(run, host, ip)
    hello = attempts[0]
    small = None
    if (
        not any(item.answered for item in attempts)
        and hello.kind in (block_cause.HELLO_TIMEOUT, block_cause.HELLO_RESET)
        and not run.dns_cancelled()
    ):
        small = block_cause.window_hello(ip, host, cancel=run.probe_cancel, timeout=SWEEP_TIMEOUT_S)
    # Чей узел: кэш у провайдера можно закрыть в hosts, узел самого Google — нельзя (на него плеер и переходит).
    owner = None if run.dns_cancelled() else video_nodes.node_owner(run, ip)
    return SweepFacts(host, ip, family, hello, small, owner, more=tuple(attempts[1:]), closed=closed)


def _addresses(run, host: str, *, closed: bool = False) -> list[tuple[str, str]]:
    """Адреса узла. Закрытый в hosts узел берётся из DNS мимо hosts и только по IPv4 (закрыт он по IPv4):
    иначе проверка постучалась бы в ``127.0.0.2`` на своём же компьютере и назвала бы кэш мёртвым."""
    from diagnostics import net_access

    found: list[tuple[str, str]] = []
    if closed:
        v4 = net_access.system_ipv4(run, host).ips or net_access.doh_lookup(run, host)[1]
        return [(v4[0], FAMILY_V4)] if v4 else []
    v4 = net_access.browser_ipv4(run, host)
    if v4:
        found.append((v4[0], FAMILY_V4))
    v6 = _ipv6_address(host)
    if v6:
        found.append((v6, FAMILY_V6))
    return found


def sweep_hosts(hosts: tuple[str, ...], closed_groups: tuple[str, ...] = ()) -> list[tuple[str, bool]]:
    """Какие узлы обходить: выданные плеером (не больше ``SWEEP_MAX_NODES``) и закрытые в hosts группы,
    которых среди них нет (``rr1---<группа>``, не больше ``SWEEP_MAX_CLOSED``). Второе — закрыт ли узел."""
    closed = {str(group).lower() for group in closed_groups if group}
    names = list(hosts[:SWEEP_MAX_NODES])
    seen = {node_group(host) for host in names}
    extra = [f"rr1---{group}.googlevideo.com" for group in sorted(closed) if group not in seen][:SWEEP_MAX_CLOSED]
    return [(host, node_group(host) in closed) for host in names + extra]


def collect(run, hosts: tuple[str, ...], closed_groups: tuple[str, ...] = ()) -> tuple[SweepFacts, ...]:
    """Приветствия каждому узлу по IPv4 и IPv6 (закрытым в hosts — по настоящему IPv4). Сеть — только здесь."""
    futures = []
    for host, closed in sweep_hosts(hosts, closed_groups):
        if run.dns_cancelled():
            break
        for ip, family in _addresses(run, host, closed=closed):
            futures.append(run.submit(_one, run, host, ip, family, closed))
    return tuple(future.result() for future in futures)
