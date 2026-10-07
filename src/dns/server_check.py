"""Проверка DNS-серверов: каждый адрес каждым способом и перехват по дороге.

Что собирается (наблюдения)
---------------------------
По каждому адресу каждого сервера, независимо друг от друга:

* пинг (ICMP) — жив ли адрес вообще;
* обычный DNS по UDP и по TCP (порт 53);
* шифрованный DNS: DoT (порт 853) и DoH (порт 443).

Провайдер может закрыть любой из способов отдельно и для отдельного адреса
(8.8.8.8 закрыт, 8.8.4.4 работает), поэтому проверяется каждая пара.

Каждым способом сервер спрашивается несколько раз подряд (``PROBE_ATTEMPTS``),
каждый раз новым соединением: блокировка иногда включается не с первого
запроса. Способ, который ответил лишь на часть запросов, остаётся рабочим, но
помечается как «отвечает через раз».

Затем у того же адреса спрашивается, **кто на самом деле отвечает**: имя
``whoami.akamai.net`` возвращает адрес сервера, который пришёл за ответом.
Вопрос задаётся дважды — обычным путём и шифрованным. И наконец несколько
часто блокируемых сайтов спрашиваются обоими путями.

Как делаются выводы
-------------------
Выводы считаются отдельно от сбора (``judge_row``, ``judge_report``) и
опираются на противоречия, а не на догадки:

* сервер по UDP говорит «сайта нет», а по шифрованному пути тот же сервер
  даёт адрес — честный сервер себе не противоречит, ответ подменён;
* обычные запросы к серверам разных владельцев выполняет одна и та же чужая
  сеть — их перехватывают и уводят на общий сервер, а обратный адрес
  подставляют, будто ответил выбранный;
* ответил адрес, где DNS-сервера нет (``utils.dns_interception``).

Молчание на пинг само по себе проблемой не считается: многие серверы на него
не отвечают.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from statistics import median
from urllib.parse import urlsplit

from utils.address_kinds import is_stub_address
from utils.dns_interception import canary_answered
from utils.dns_reference import REFERENCE_RESOLVERS
from utils.dns_wire import (
    FAILURE_CANCELLED,
    FAILURE_CLOSED,
    FAILURE_RESET,
    FAILURE_TIMEOUT,
    FAILURE_TLS,
    STATUS_NXDOMAIN,
    TRANSPORT_DOH,
    TRANSPORT_DOT,
    TRANSPORT_TCP,
    TRANSPORT_UDP,
    TYPE_A,
    DnsQueryResult,
    failure_text,
    query_doh,
    query_dot,
    query_tcp,
    query_udp,
)
from utils.ip_owner import IpOwner, lookup_ip_owner
from utils.socket_cancel import SocketCancel

TRANSPORT_ICMP = "icmp"
# Порядок столбцов таблицы.
TRANSPORTS = (TRANSPORT_ICMP, TRANSPORT_UDP, TRANSPORT_TCP, TRANSPORT_DOT, TRANSPORT_DOH)

STATE_OK = "ok"
STATE_FAIL = "fail"
# Способ к этому серверу не применим: нет имени для шифрования или нет пинга в системе.
STATE_SKIP = "skip"
# Проверку сняли раньше, чем пришёл ответ.
STATE_UNKNOWN = "unknown"

LEVEL_OK = "ok"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_FAIL = "fail"

CODE_INTERCEPTED = "intercepted"
CODE_SPOOFED = "spoofed"
CODE_FOREIGN_ANSWERS = "foreign_answers"
CODE_DEAD = "dead"
CODE_UDP_BLOCKED = "udp_blocked"
CODE_TCP_BLOCKED = "tcp_blocked"
CODE_DOT_BLOCKED = "dot_blocked"
CODE_DOH_BLOCKED = "doh_blocked"
CODE_DOH_NAME_BLOCKED = "doh_name_blocked"
CODE_UNSTABLE = "unstable"
CODE_SELF_FILTER = "self_filter"
CODE_BEST = "best"
CODE_BYPASS_RUNNING = "bypass_running"
CODE_ALL_FINE = "all_fine"

# Безобидное имя для самой проверки связи и замера времени.
PROBE_DOMAIN = "example.com"
# Akamai отвечает на это имя адресом сервера, который пришёл за ответом.
WHOAMI_DOMAIN = "whoami.akamai.net"
# Сайты, ответы про которые подменяют чаще всего. Набор взят из проекта
# dpi-detector (github.com/Runnin4ik/dpi-detector, лицензия MIT, © Runnin4ik).
SENSITIVE_DOMAINS = ("rutor.info", "flibusta.is", "rezka.ag")

UDP_TIMEOUT_S = 1.5
# На Windows отказ «порт закрыт» приходит примерно через две секунды.
TCP_TIMEOUT_S = 4.0
ENCRYPTED_TIMEOUT_S = 4.0
# Через сколько секунд без ответа по имени пробуем тот же сервер DoH без имени.
DOH_WITHOUT_NAME_AFTER_S = 1.0
PING_COUNT = 2
PING_TIMEOUT_MS = 1500
# Сколько раз подряд спрашиваем сервер каждым способом. Блокировка иногда
# включается не с первого запроса: первый проходит, а второй или третий уже нет.
PROBE_ATTEMPTS = 3
# Почти все запросы — ожидание сети, поэтому адресов сразу много: общее время
# проверки упирается в самый медленный (молчащий) адрес, а не в их число.
MAX_PARALLEL_ADDRESSES = 96
RUN_DEADLINE_S = 75.0

# После такого сбоя DoH по имени стоит попробовать тот же сервер без имени:
# если так он отвечает, значит закрыто именно имя.
_NAME_BLOCK_FAILURES = (FAILURE_TIMEOUT, FAILURE_RESET, FAILURE_CLOSED, FAILURE_TLS)


@dataclass(frozen=True, slots=True)
class CheckTarget:
    """Один адрес сервера и то, что нужно для шифрованных запросов к нему."""

    provider: str
    address: str
    dot_host: str = ""
    doh_host: str = ""
    doh_port: int = 443
    doh_path: str = "/dns-query"
    # Значок и цвет сервера из каталога — только для показа.
    icon: str = ""
    color: str = ""


@dataclass(frozen=True, slots=True)
class Cell:
    """Итог одного способа связи с одним адресом."""

    state: str = STATE_UNKNOWN
    elapsed_ms: float | None = None
    # У ответившего способа — причина сорвавшихся запросов серии, если такие были.
    failure: str = ""
    reason: str = ""
    # Чем закончился каждый запрос серии, по порядку. Пусто, если серии не было (пинг).
    trail: tuple[bool, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == STATE_OK

    @property
    def attempts(self) -> int:
        return len(self.trail)

    @property
    def answered(self) -> int:
        return sum(self.trail)

    @property
    def unstable(self) -> bool:
        """Способ работает, но часть запросов серии сорвалась."""
        return self.state == STATE_OK and not all(self.trail)

    @property
    def fades(self) -> bool:
        """Первые запросы прошли, а дальше связь закрылась: так включается блокировка."""
        return self.unstable and self.trail[0] and not self.trail[-1]

    @property
    def failed(self) -> bool:
        return self.state == STATE_FAIL


@dataclass(frozen=True, slots=True)
class DomainFact:
    """Что один и тот же сервер ответил про сайт обычным и шифрованным путём."""

    domain: str
    udp_status: str = ""
    udp_ips: tuple[str, ...] = ()
    secure_status: str = ""
    secure_ips: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Finding:
    """Вывод проверки. ``text`` — фраза целиком, для журнала и текстового отчёта.

    Остальное — та же находка готовыми частями для экрана, чтобы он не резал
    фразу сам: короткий заголовок, полный перечень серверов (название сервиса
    и адрес, без обрезки) и пояснение.
    """

    level: str
    code: str
    text: str
    title: str = ""
    servers: tuple[tuple[str, str], ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class Observation:
    """Все факты об одном адресе. Выводов здесь нет."""

    target: CheckTarget
    cells: tuple[tuple[str, Cell], ...] = ()
    # DoH без имени сервера: проверяется, только если по имени он не ответил.
    doh_by_address: Cell | None = None
    # Кто выполнил запрос «кто ты»: обычным путём и шифрованным.
    udp_egress: str = ""
    secure_egress: str = ""
    # Каким шифрованным (или хотя бы не UDP) путём сервер отвечал на контрольные вопросы.
    secure_via: str = ""
    domains: tuple[DomainFact, ...] = ()
    findings: tuple[Finding, ...] = ()

    def cell(self, transport: str) -> Cell:
        for name, cell in self.cells:
            if name == transport:
                return cell
        return Cell()


@dataclass(frozen=True, slots=True)
class ServerCheckReport:
    rows: tuple[Observation, ...] = ()
    total: int = 0
    # Ответил ли адрес, где DNS-сервера нет. None — ещё не проверено.
    canary: bool | None = None
    owners: tuple[tuple[str, IpOwner], ...] = ()
    # Программы обхода и VPN, работавшие во время проверки (вместе с Zapret).
    bypass: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    finished: bool = False
    stopped: bool = False
    timed_out: bool = False
    elapsed_s: float = 0.0

    def owner_of(self, ip: str) -> IpOwner | None:
        for address, owner in self.owners:
            if address == ip:
                return owner
        return None


Progress = Callable[[ServerCheckReport], None]
ShouldStop = Callable[[], bool]


# ---------------------------------------------------------------------------
# Что проверять
# ---------------------------------------------------------------------------


def build_targets(providers: dict, *, ipv6: bool = False) -> tuple[CheckTarget, ...]:
    """Адреса для проверки из списка серверов программы (со своими серверами пользователя)."""
    targets: list[CheckTarget] = []
    seen: set[str] = set()
    for group in providers.values():
        for name, data in group.items():
            doh = urlsplit(str(data.get("doh") or "").strip())
            addresses = [*(data.get("ipv4") or ())]
            if ipv6:
                addresses.extend(data.get("ipv6") or ())
            for raw in addresses:
                address = str(raw or "").strip()
                if not address or address in seen:
                    continue
                seen.add(address)
                targets.append(
                    CheckTarget(
                        provider=str(name),
                        address=address,
                        dot_host=str(data.get("dot") or "").strip(),
                        doh_host=doh.hostname or "",
                        doh_port=doh.port or 443,
                        doh_path=doh.path or "/dns-query",
                        icon=str(data.get("icon") or "").strip(),
                        color=str(data.get("color") or "").strip(),
                    )
                )
    return tuple(targets)


# ---------------------------------------------------------------------------
# Сбор фактов
# ---------------------------------------------------------------------------


def _cell(result: DnsQueryResult) -> Cell:
    if result.answered:
        return Cell(state=STATE_OK, elapsed_ms=result.elapsed_ms)
    if result.failure == FAILURE_CANCELLED:
        return Cell()
    return Cell(state=STATE_FAIL, failure=result.failure, reason=failure_text(result))


def _ping(address: str, should_stop: ShouldStop) -> Cell:
    from utils.windows_icmp import ping_ipv4_host_winapi, ping_ipv6_winapi

    if ":" in address:
        result = ping_ipv6_winapi(address, count=PING_COUNT, timeout_ms=PING_TIMEOUT_MS, cancelled=should_stop)
    else:
        result = ping_ipv4_host_winapi(
            address, count=PING_COUNT, timeout_ms=PING_TIMEOUT_MS, resolved_ip=address, cancelled=should_stop
        )
    if result.ok:
        return Cell(state=STATE_OK, elapsed_ms=result.average_ms)
    if result.error_code == "UNSUPPORTED":
        return Cell(state=STATE_SKIP, reason="пинг в этой системе недоступен")
    if should_stop():
        return Cell()
    return Cell(state=STATE_FAIL, failure=FAILURE_TIMEOUT, reason="не отвечает на пинг")


def _twice(ask: Callable[[], DnsQueryResult]) -> DnsQueryResult:
    """Запрос с одним повтором после молчания: одиночная потеря — ещё не «закрыто»."""
    result = ask()
    if not result.answered and result.failure == FAILURE_TIMEOUT:
        result = ask()
    return result


def _series(
    ask: Callable[[], DnsQueryResult],
    *,
    retry_silence: bool = False,
    on_first: Callable[[DnsQueryResult], None] | None = None,
) -> Cell:
    """Несколько запросов подряд одним способом, каждый — новым соединением.

    Ответивший способ спрашиваем ``PROBE_ATTEMPTS`` раз — так видна блокировка,
    которая включается со второго или третьего запроса. После сорвавшегося
    запроса серия продолжается: если следующий прошёл, это была случайная
    потеря, а если нет — связь действительно закрылась.

    ``retry_silence`` — повторить первый запрос, если на него промолчали. Нужно
    только для UDP: там потерянный пакет никто не пересылает. В TCP, DoT и DoH
    потери за срок ожидания исправляет сама система, и молчание там — уже ответ.
    """
    results = [ask()]
    if on_first is not None:
        on_first(results[0])
    if not results[0].answered:
        if retry_silence and results[0].failure == FAILURE_TIMEOUT:
            results.append(ask())
        if not results[-1].answered:
            failed = _cell(results[-1])
            return replace(failed, trail=(False,) * len(results)) if failed.failed else failed
    while len(results) < PROBE_ATTEMPTS:
        result = ask()
        if result.failure == FAILURE_CANCELLED:
            break
        results.append(result)
    times = [result.elapsed_ms for result in results if result.answered and result.elapsed_ms is not None]
    lost = next((result for result in reversed(results) if not result.answered), None)
    return Cell(
        state=STATE_OK,
        elapsed_ms=median(times) if times else None,
        failure=lost.failure if lost is not None else "",
        reason=failure_text(lost) if lost is not None else "",
        trail=tuple(result.answered for result in results),
    )


def _once_udp(address: str, domain: str, cancel: SocketCancel) -> DnsQueryResult:
    return query_udp(address, domain, TYPE_A, timeout_s=UDP_TIMEOUT_S, cancel=cancel)


def _ask_udp(address: str, domain: str, cancel: SocketCancel) -> DnsQueryResult:
    return _twice(lambda: _once_udp(address, domain, cancel))


def _once_doh(target: CheckTarget, domain: str, cancel: SocketCancel, *, by_name: bool = True) -> DnsQueryResult:
    return query_doh(
        target.address,
        domain,
        TYPE_A,
        tls_host=target.doh_host if by_name else "",
        port=target.doh_port,
        path=target.doh_path,
        timeout_s=ENCRYPTED_TIMEOUT_S,
        cancel=cancel,
    )


def _once_dot(target: CheckTarget, domain: str, cancel: SocketCancel) -> DnsQueryResult:
    return query_dot(
        target.address, domain, TYPE_A, tls_host=target.dot_host, timeout_s=ENCRYPTED_TIMEOUT_S, cancel=cancel
    )


def _once_tcp(target: CheckTarget, domain: str, cancel: SocketCancel) -> DnsQueryResult:
    return query_tcp(target.address, domain, TYPE_A, timeout_s=TCP_TIMEOUT_S, cancel=cancel)


def _secure_path(observation: Observation) -> tuple[str, Callable[[CheckTarget, str, SocketCancel], DnsQueryResult] | None]:
    """Самый надёжный из ответивших путей в обход обычного UDP.

    Запрос этим путём не повторяется: потери в TCP исправляет сама система, а
    второй срок ожидания на каждый контрольный вопрос удваивал время проверки.
    """
    if observation.cell(TRANSPORT_DOH).ok:
        return TRANSPORT_DOH, _once_doh
    if observation.doh_by_address is not None and observation.doh_by_address.ok:
        return TRANSPORT_DOH, lambda target, domain, cancel: _once_doh(target, domain, cancel, by_name=False)
    if observation.cell(TRANSPORT_DOT).ok:
        return TRANSPORT_DOT, _once_dot
    if observation.cell(TRANSPORT_TCP).ok:
        return TRANSPORT_TCP, _once_tcp
    return "", None


def _first_address(result: DnsQueryResult | None) -> str:
    if result is None:
        return ""
    values = result.values(TYPE_A)
    return values[0] if values else ""


def probe_address(target: CheckTarget, cancel: SocketCancel, should_stop: ShouldStop) -> Observation:
    """Все факты об одном адресе. Сеть — только здесь."""
    skip = Cell(state=STATE_SKIP, reason="сервер не объявлял этот способ")
    with ThreadPoolExecutor(max_workers=10, thread_name_prefix="dns-server") as pool:
        # DoH без имени сервера нужен, только если по имени он не ответил. Чтобы
        # молчащий адрес не ждал два срока подряд, этот запрос уходит, как только
        # первый запрос по имени сорвался или не уложился в DOH_WITHOUT_NAME_AFTER_S.
        first_by_name: list[DnsQueryResult] = []
        first_done = threading.Event()

        def note_first(result: DnsQueryResult) -> None:
            first_by_name.append(result)
            first_done.set()

        def ask_without_name() -> DnsQueryResult | None:
            if first_done.wait(DOH_WITHOUT_NAME_AFTER_S):
                first = first_by_name[0]
                if first.answered or first.failure not in _NAME_BLOCK_FAILURES:
                    return None
            return _once_doh(target, PROBE_DOMAIN, cancel, by_name=False)

        icmp = pool.submit(_ping, target.address, should_stop)
        udp = pool.submit(_series, lambda: _once_udp(target.address, PROBE_DOMAIN, cancel), retry_silence=True)
        tcp = pool.submit(_series, lambda: _once_tcp(target, PROBE_DOMAIN, cancel))
        dot = pool.submit(_series, lambda: _once_dot(target, PROBE_DOMAIN, cancel)) if target.dot_host else None
        doh = without_name = None
        if target.doh_host:
            doh = pool.submit(_series, lambda: _once_doh(target, PROBE_DOMAIN, cancel), on_first=note_first)
            without_name = pool.submit(ask_without_name)

        doh_cell = doh.result() if doh is not None else skip
        doh_by_address: Cell | None = None
        answer = without_name.result() if without_name is not None else None
        if answer is not None and doh_cell.failed and doh_cell.failure in _NAME_BLOCK_FAILURES:
            doh_by_address = _cell(answer)

        observation = Observation(
            target=target,
            cells=(
                (TRANSPORT_ICMP, icmp.result()),
                (TRANSPORT_UDP, udp.result()),
                (TRANSPORT_TCP, tcp.result()),
                (TRANSPORT_DOT, dot.result() if dot is not None else skip),
                (TRANSPORT_DOH, doh_cell),
            ),
            doh_by_address=doh_by_address,
        )
        if should_stop():
            return observation

        udp_ok = observation.cell(TRANSPORT_UDP).ok
        secure_via, ask_secure = _secure_path(observation)
        # Сравнивать нечего, если нет одного из двух путей.
        if not udp_ok or ask_secure is None:
            return replace(observation, secure_via=secure_via)

        whoami_udp = pool.submit(_ask_udp, target.address, WHOAMI_DOMAIN, cancel)
        whoami_secure = pool.submit(ask_secure, target, WHOAMI_DOMAIN, cancel)
        pairs = [
            (domain, pool.submit(_ask_udp, target.address, domain, cancel), pool.submit(ask_secure, target, domain, cancel))
            for domain in SENSITIVE_DOMAINS
        ]
        domains = []
        for domain, udp_future, secure_future in pairs:
            udp_answer, secure_answer = udp_future.result(), secure_future.result()
            domains.append(
                DomainFact(
                    domain=domain,
                    udp_status=udp_answer.status,
                    udp_ips=udp_answer.values(TYPE_A),
                    secure_status=secure_answer.status,
                    secure_ips=secure_answer.values(TYPE_A),
                )
            )
        return replace(
            observation,
            secure_via=secure_via,
            udp_egress=_first_address(whoami_udp.result()),
            secure_egress=_first_address(whoami_secure.result()),
            domains=tuple(domains),
        )


def _reference_ask(cancel: SocketCancel) -> Callable[[str, int], DnsQueryResult | None]:
    """Служебный вопрос шифрованным путём: о владельце сети не должен отвечать перехватчик."""

    def ask(name: str, rtype: int) -> DnsQueryResult | None:
        last = None
        for resolver in REFERENCE_RESOLVERS:
            last = query_doh(resolver.address, name, rtype, timeout_s=ENCRYPTED_TIMEOUT_S, cancel=cancel)
            if last.answered or last.failure == FAILURE_CANCELLED:
                return last
        return last

    return ask


def _lookup_owners(rows: Iterable[Observation], cancel: SocketCancel) -> tuple[tuple[str, IpOwner], ...]:
    addresses = list(
        dict.fromkeys(ip for row in rows for ip in (row.udp_egress, row.secure_egress) if ip)
    )
    if not addresses:
        return ()
    ask = _reference_ask(cancel)
    with ThreadPoolExecutor(max_workers=min(24, len(addresses)), thread_name_prefix="dns-owner") as pool:
        found = list(pool.map(lambda ip: lookup_ip_owner(ip, ask), addresses))
    return tuple((ip, owner) for ip, owner in zip(addresses, found) if owner is not None)


# ---------------------------------------------------------------------------
# Выводы
# ---------------------------------------------------------------------------


def _owner_name(owner: IpOwner | None) -> str:
    if owner is None:
        return ""
    return owner.owner or (f"AS{owner.asn}" if owner.asn else "")


def _foreign_network(row: Observation, owner_of) -> IpOwner | None:
    """Сеть, которая отвечает на обычные запросы вместо сервера (или None)."""
    udp_owner, secure_owner = owner_of(row.udp_egress), owner_of(row.secure_egress)
    if udp_owner is None or secure_owner is None or not udp_owner.asn or not secure_owner.asn:
        return None
    return udp_owner if udp_owner.asn != secure_owner.asn else None


def _spoofed_domains(row: Observation) -> list[str]:
    """Сайты, про которые сервер обычным путём ответил не то, что шифрованным.

    Молчание на обычный запрос подменой не считается: сервер мог просто долго
    искать ответ. Считается только явное «сайта нет» или адрес-заглушка.
    """
    spoofed: list[str] = []
    for fact in row.domains:
        genuine = [ip for ip in fact.secure_ips if not is_stub_address(ip)]
        if not genuine:
            # Шифрованным путём сервер адреса не дал: сравнивать не с чем.
            continue
        if fact.udp_status == STATUS_NXDOMAIN or any(is_stub_address(ip) for ip in fact.udp_ips):
            spoofed.append(fact.domain)
    return spoofed


def resolvable_domains(rows: Iterable[Observation]) -> frozenset[str]:
    """Контрольные сайты, настоящий адрес которых шифрованным путём дал хоть один сервер."""
    return frozenset(
        fact.domain
        for row in rows
        for fact in row.domains
        if any(not is_stub_address(ip) for ip in fact.secure_ips)
    )


def _self_filtered_domains(row: Observation, resolvable: frozenset[str]) -> list[str]:
    """Сайты, которые сервер не отдаёт сам: «сайта нет» или заглушка даже по шифрованному пути.

    Шифрованный ответ по дороге не подменить, значит так решил сам сервер. Чтобы
    не записать в фильтрацию сайт, которого и правда нет, берём только сайты,
    настоящий адрес которых в этой же проверке дал другой сервер.
    """
    filtered: list[str] = []
    for fact in row.domains:
        if fact.domain not in resolvable:
            continue
        stub = bool(fact.secure_ips) and all(is_stub_address(ip) for ip in fact.secure_ips)
        if fact.secure_status == STATUS_NXDOMAIN or stub:
            filtered.append(fact.domain)
    return filtered


_SERIES_TITLES = ("обычный DNS (UDP)", "DNS по TCP", "DoT", "DoH")


def _unstable_level(shaky: list[tuple[str, Cell]]) -> str:
    """Один запрос без ответа — случайная потеря. Обрыв или два срыва из трёх — уже не случайность."""
    for _title, cell in shaky:
        if cell.attempts - cell.answered >= 2 or cell.failure != FAILURE_TIMEOUT:
            return LEVEL_WARN
    return LEVEL_INFO


def judge_row(row: Observation, owner_of, resolvable: frozenset[str] = frozenset()) -> tuple[Finding, ...]:
    """Выводы об одном адресе. Только по собранным фактам, без сети.

    ``resolvable`` — контрольные сайты, которые точно существуют (см.
    ``resolvable_domains``): по ним видно, что сервер фильтрует ответы сам.
    """
    findings: list[Finding] = []
    udp, tcp = row.cell(TRANSPORT_UDP), row.cell(TRANSPORT_TCP)
    dot, doh = row.cell(TRANSPORT_DOT), row.cell(TRANSPORT_DOH)
    dns_cells = [cell for cell in (udp, tcp, dot, doh) if cell.state in (STATE_OK, STATE_FAIL)]

    if dns_cells and all(cell.failed for cell in dns_cells):
        alive = " (на пинг при этом отвечает)" if row.cell(TRANSPORT_ICMP).ok else ""
        return (Finding(LEVEL_FAIL, CODE_DEAD, f"не отвечает ни одним способом{alive}"),)

    spoofed = _spoofed_domains(row)
    if spoofed:
        findings.append(
            Finding(
                LEVEL_FAIL,
                CODE_SPOOFED,
                f"обычные ответы подменяются: про {', '.join(spoofed)} приходит «сайта нет» или заглушка, "
                "хотя по шифрованному пути тот же сервер даёт настоящий адрес",
            )
        )
    filtered = _self_filtered_domains(row, resolvable)
    if filtered:
        findings.append(
            Finding(
                LEVEL_INFO,
                CODE_SELF_FILTER,
                f"сам не отдаёт адреса сайтов: {', '.join(filtered)} — так он отвечает и по шифрованному пути, "
                "который по дороге не подменить; это решение самого сервера, а не провайдера",
            )
        )
    foreign = _foreign_network(row, owner_of)
    if foreign is not None:
        # Само по себе это не тревога: так устроены и некоторые серверы. Перехват
        # признаётся в общем итоге, когда одна сеть отвечает за разных владельцев.
        findings.append(
            Finding(
                LEVEL_INFO,
                CODE_FOREIGN_ANSWERS,
                f"обычные запросы выполняет сеть {_owner_name(foreign)}, "
                f"а шифрованные — {_owner_name(owner_of(row.secure_egress))}",
            )
        )
    if udp.failed:
        findings.append(Finding(LEVEL_FAIL, CODE_UDP_BLOCKED, f"обычный DNS (UDP) закрыт: {udp.reason}"))
    if tcp.failed and not udp.failed:
        findings.append(Finding(LEVEL_INFO, CODE_TCP_BLOCKED, f"DNS по TCP не проходит: {tcp.reason}"))
    if dot.failed:
        findings.append(Finding(LEVEL_WARN, CODE_DOT_BLOCKED, f"шифрованный DNS по DoT закрыт: {dot.reason}"))
    if doh.failed:
        if row.doh_by_address is not None and row.doh_by_address.ok:
            findings.append(
                Finding(
                    LEVEL_WARN,
                    CODE_DOH_NAME_BLOCKED,
                    f"шифрованный DNS по DoH закрыт по имени {row.target.doh_host}: "
                    "без имени тот же адрес отвечает — так блокируют по имени сервера",
                )
            )
        else:
            findings.append(Finding(LEVEL_WARN, CODE_DOH_BLOCKED, f"шифрованный DNS по DoH закрыт: {doh.reason}"))
    shaky = [(title, cell) for title, cell in zip(_SERIES_TITLES, (udp, tcp, dot, doh)) if cell.unstable]
    if shaky:
        parts = [f"{title} — {cell.answered} из {cell.attempts} запросов ({cell.reason})" for title, cell in shaky]
        fades = " Первые запросы проходят, а следующие уже нет." if any(cell.fades for _title, cell in shaky) else ""
        findings.append(Finding(_unstable_level(shaky), CODE_UNSTABLE, f"отвечает через раз: {'; '.join(parts)}.{fades}"))
    return tuple(findings)


def _label(row: Observation) -> str:
    return f"{row.target.provider} ({row.target.address})"


def _pair(row: Observation) -> tuple[str, str]:
    return row.target.provider, row.target.address


def _labels(servers: Iterable[tuple[str, str]]) -> list[str]:
    return [f"{provider} ({address})" for provider, address in servers]


def _short_list(items: list[str], limit: int = 4) -> str:
    shown = ", ".join(items[:limit])
    return f"{shown} и ещё {len(items) - limit}" if len(items) > limit else shown


def _shared_foreign_network(rows: Iterable[Observation], owner_of) -> tuple[IpOwner | None, list[str]]:
    """Чужая сеть, которая отвечает на обычные запросы серверам разных владельцев.

    Владельца сервера выдаёт сеть, через которую он выполняет шифрованные
    запросы: её по дороге не подменить. Два названия одного владельца (у них
    эта сеть общая) перехватом не считаются — так сервер может быть устроен сам.
    """
    providers_by_asn: dict[str, list[str]] = {}
    operators_by_asn: dict[str, set[str]] = {}
    owners: dict[str, IpOwner] = {}
    for row in rows:
        foreign = _foreign_network(row, owner_of)
        if foreign is None:
            continue
        owners[foreign.asn] = foreign
        operators_by_asn.setdefault(foreign.asn, set()).add(owner_of(row.secure_egress).asn)
        names = providers_by_asn.setdefault(foreign.asn, [])
        if row.target.provider not in names:
            names.append(row.target.provider)
    for asn, operators in operators_by_asn.items():
        if len(operators) >= 2:
            return owners[asn], providers_by_asn[asn]
    return None, []


def _has(row: Observation, code: str, level: str = "") -> bool:
    return any(finding.code == code and (not level or finding.level == level) for finding in row.findings)


def _without_remarks(row: Observation) -> bool:
    return not any(finding.level in (LEVEL_WARN, LEVEL_FAIL) for finding in row.findings)


# Меньше адресов — не «закрыт весь способ», а совпадение.
_WHOLESALE_MIN = 3


def _transport_summary(
    rows: tuple[Observation, ...], transport: str, title: str, codes: tuple[str, ...], code: str
) -> Finding | None:
    """Одна находка о закрытом способе связи: «закрыт целиком» или у каких серверов."""
    tried = [row for row in rows if row.cell(transport).state in (STATE_OK, STATE_FAIL)]
    closed = [row for row in tried if any(finding.code in codes for finding in row.findings)]
    if not closed:
        return None
    if len(closed) == len(tried) and len(tried) >= _WHOLESALE_MIN:
        note = f"не ответил ни один из {len(tried)} адресов."
        return Finding(
            LEVEL_WARN, code, f"{title} закрыт целиком: {note}", title=f"{title} закрыт целиком", note=_capital(note)
        )
    servers = tuple(_pair(row) for row in closed)
    head = f"{title} закрыт у части серверов"
    return Finding(LEVEL_WARN, code, f"{head}: {_short_list(_labels(servers))}.", title=head, servers=servers)


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def judge_report(
    rows: tuple[Observation, ...],
    canary: bool | None,
    owner_of,
    bypass: tuple[str, ...] = (),
) -> tuple[Finding, ...]:
    """Общий итог по всем адресам: сначала самое важное."""
    findings: list[Finding] = []
    if bypass:
        # Первой строкой: от этого зависит, как читать всё остальное.
        note = (
            f"{', '.join(bypass)}. Такие программы меняют соединения и DNS, "
            "поэтому результат показывает сеть вместе с ними, а не «чистую» сеть провайдера."
        )
        head = "Во время проверки работали"
        findings.append(Finding(LEVEL_INFO, CODE_BYPASS_RUNNING, f"{head}: {note}", title=head, note=note))
    network, providers = _shared_foreign_network(rows, owner_of)
    if canary or network is not None:
        evidence: list[str] = []
        if canary:
            evidence.append("ответил адрес, где DNS-сервера нет")
        if network is not None:
            evidence.append(
                f"обычные запросы к {_short_list(providers)} на самом деле выполняет одна сеть — {_owner_name(network)}"
            )
        head = "Обычные DNS-запросы перехватываются по дороге (провайдером или роутером)"
        note = "; ".join(evidence) + ". Какой бы сервер вы ни выбрали, без шифрования отвечает перехватчик."
        findings.append(Finding(LEVEL_FAIL, CODE_INTERCEPTED, f"{head}: {note}", title=head, note=_capital(note)))

    spoofed = tuple(_pair(row) for row in rows if _has(row, CODE_SPOOFED))
    if spoofed:
        head = "Обычные ответы подменяются у серверов"
        findings.append(
            Finding(LEVEL_FAIL, CODE_SPOOFED, f"{head}: {_short_list(_labels(spoofed))}.", title=head, servers=spoofed)
        )

    closed = (
        _transport_summary(
            rows, TRANSPORT_DOT, "Шифрованный DNS по DoT (порт 853)", (CODE_DOT_BLOCKED,), CODE_DOH_BLOCKED
        ),
        _transport_summary(
            rows,
            TRANSPORT_DOH,
            "Шифрованный DNS по DoH (порт 443)",
            (CODE_DOH_BLOCKED, CODE_DOH_NAME_BLOCKED),
            CODE_DOH_BLOCKED,
        ),
        _transport_summary(rows, TRANSPORT_UDP, "Обычный DNS (UDP, порт 53)", (CODE_UDP_BLOCKED,), CODE_UDP_BLOCKED),
    )
    findings.extend(finding for finding in closed if finding is not None)
    # Случайные потери и настоящие срывы — разные вещи: первые только считаем, вторые называем.
    shaky = tuple(_pair(row) for row in rows if _has(row, CODE_UNSTABLE, LEVEL_WARN))
    if shaky:
        head, note = "Отвечают через раз", "Так бывает, когда блокировка включается не с первого запроса."
        findings.append(
            Finding(
                LEVEL_WARN,
                CODE_UNSTABLE,
                f"{head}: {_short_list(_labels(shaky))}. {note}",
                title=head,
                servers=shaky,
                note=note,
            )
        )
    lossy = sum(1 for row in rows if _has(row, CODE_UNSTABLE, LEVEL_INFO))
    if lossy:
        head = "Потеряли по одному запросу"
        note = f"{lossy} из {len(rows)} адресов. Похоже на обычные потери в сети."
        findings.append(Finding(LEVEL_INFO, CODE_UNSTABLE, f"{head}: {note}", title=head, note=note))
    filtering = tuple(_pair(row) for row in rows if _has(row, CODE_SELF_FILTER))
    if filtering:
        # Фильтрует сервис целиком, поэтому во фразе — названия без повторов; адреса — в перечне.
        names = list(dict.fromkeys(provider for provider, _address in filtering))
        head = "Сами не отдают часть сайтов"
        note = "Это решение самих серверов: так они отвечают и по шифрованному пути."
        findings.append(
            Finding(
                LEVEL_INFO,
                CODE_SELF_FILTER,
                f"{head}: {_short_list(names)}. {note}",
                title=head,
                servers=filtering,
                note=note,
            )
        )
    dead = tuple(_pair(row) for row in rows if _has(row, CODE_DEAD))
    if dead:
        head = "Не отвечают совсем"
        findings.append(Finding(LEVEL_INFO, CODE_DEAD, f"{head}: {_short_list(_labels(dead))}.", title=head, servers=dead))

    # Windows шифрует запросы по DoH и по имени сервера: годится только тот, кто так отвечает.
    usable = [
        row
        for row in rows
        if row.cell(TRANSPORT_DOH).ok
        and not row.cell(TRANSPORT_DOH).unstable
        and not any(finding.code in (CODE_SPOOFED, CODE_SELF_FILTER) for finding in row.findings)
    ]
    if usable:
        best = min(usable, key=lambda row: row.cell(TRANSPORT_DOH).elapsed_ms or float("inf"))
        clean = sum(1 for row in rows if _without_remarks(row))
        note = (
            f"шифрованный запрос проходит за {max(1, round(best.cell(TRANSPORT_DOH).elapsed_ms or 0))} мс. "
            f"Без замечаний: {clean} из {len(rows)} адресов."
        )
        findings.append(
            Finding(
                LEVEL_OK,
                CODE_BEST,
                f"Для защищённого DNS сейчас лучше всего подходит {_label(best)}: {note}",
                title="Для защищённого DNS сейчас лучше всего подходит",
                servers=(_pair(best),),
                note=_capital(note),
            )
        )
    elif rows and not any(finding.level in (LEVEL_WARN, LEVEL_FAIL, LEVEL_OK) for finding in findings) and not any(
        finding.code == CODE_DEAD for finding in findings
    ):
        findings.append(Finding(LEVEL_OK, CODE_ALL_FINE, "Замечаний нет.", title="Замечаний нет"))
    return tuple(findings)


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _State:
    rows: dict[int, Observation] = field(default_factory=dict)
    canary: bool | None = None


def run_server_check(
    targets: Iterable[CheckTarget],
    *,
    on_progress: Progress | None = None,
    should_stop: ShouldStop | None = None,
    bypass: Iterable[str] = (),
) -> ServerCheckReport:
    """Проверяет все адреса и возвращает отчёт. Промежуточные отчёты уходят в ``on_progress``.

    ``bypass`` — программы обхода и VPN, запущенные сейчас: они попадают в отчёт
    как оговорка к результатам.
    """
    targets = tuple(targets)
    bypass = tuple(bypass)
    started = time.monotonic()
    cancel = SocketCancel()
    deadline = started + RUN_DEADLINE_S

    def stopped() -> bool:
        try:
            return bool(should_stop is not None and should_stop())
        except Exception:
            return False

    def halted() -> bool:
        return cancel.cancelled or stopped()

    state = _State()

    def snapshot(**extra) -> ServerCheckReport:
        return ServerCheckReport(
            rows=tuple(state.rows[index] for index in sorted(state.rows)),
            total=len(targets),
            canary=state.canary,
            bypass=bypass,
            elapsed_s=time.monotonic() - started,
            **extra,
        )

    def publish(report: ServerCheckReport) -> ServerCheckReport:
        if on_progress is not None:
            on_progress(report)
        return report

    user_stopped = timed_out = False
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_ADDRESSES + 1, thread_name_prefix="dns-check")
    try:
        pending: dict[Future, object] = {pool.submit(canary_answered, PROBE_DOMAIN, cancel=cancel): "canary"}
        for index, target in enumerate(targets):
            pending[pool.submit(probe_address, target, cancel, halted)] = index
        publish(snapshot())

        while pending:
            done, _waiting = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            if stopped():
                user_stopped = True
            elif time.monotonic() >= deadline:
                timed_out = True
            if user_stopped or timed_out:
                cancel.cancel()
            for future in done:
                key = pending.pop(future)
                try:
                    result = future.result()
                except Exception:
                    continue
                if key == "canary":
                    # Снятый запрос ничего не значит: «не ответил» было бы неправдой.
                    state.canary = None if cancel.cancelled else bool(result)
                elif isinstance(result, Observation):
                    state.rows[int(key)] = result
            if done and not cancel.cancelled:
                publish(snapshot())
            if cancel.cancelled and not done:
                # Запросы уже сняты: ждать нечего, остаток вернётся сам.
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    rows = tuple(state.rows[index] for index in sorted(state.rows))
    owners: tuple[tuple[str, IpOwner], ...] = ()
    if not user_stopped:
        owners = _lookup_owners(rows, SocketCancel())
    owner_map = dict(owners)
    resolvable = resolvable_domains(rows)
    rows = tuple(replace(row, findings=judge_row(row, owner_map.get, resolvable)) for row in rows)
    report = ServerCheckReport(
        rows=rows,
        total=len(targets),
        canary=state.canary,
        owners=owners,
        bypass=bypass,
        findings=() if user_stopped else judge_report(rows, state.canary, owner_map.get, bypass),
        finished=True,
        stopped=user_stopped,
        timed_out=timed_out,
        elapsed_s=time.monotonic() - started,
    )
    return publish(report)


__all__ = [
    "CODE_ALL_FINE",
    "CODE_BEST",
    "CODE_BYPASS_RUNNING",
    "CODE_DEAD",
    "CODE_DOH_BLOCKED",
    "CODE_DOH_NAME_BLOCKED",
    "CODE_DOT_BLOCKED",
    "CODE_FOREIGN_ANSWERS",
    "CODE_INTERCEPTED",
    "CODE_SELF_FILTER",
    "CODE_SPOOFED",
    "CODE_TCP_BLOCKED",
    "CODE_UDP_BLOCKED",
    "CODE_UNSTABLE",
    "LEVEL_FAIL",
    "LEVEL_INFO",
    "LEVEL_OK",
    "LEVEL_WARN",
    "SENSITIVE_DOMAINS",
    "STATE_FAIL",
    "STATE_OK",
    "STATE_SKIP",
    "STATE_UNKNOWN",
    "TRANSPORTS",
    "TRANSPORT_ICMP",
    "Cell",
    "CheckTarget",
    "DomainFact",
    "Finding",
    "Observation",
    "ServerCheckReport",
    "build_targets",
    "judge_report",
    "judge_row",
    "resolvable_domains",
    "probe_address",
    "run_server_check",
]
