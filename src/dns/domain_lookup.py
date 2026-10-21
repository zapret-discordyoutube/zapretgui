"""Проверка одного домена или адреса: пинг, ответы разных DNS, соседи по адресу.

Пользователь вводит домен или IP. Модуль:

1. спрашивает адреса домена у каждого DNS-сервера из списка программы (и у
   системного) напрямую — так видно, кто что отвечает и не подставляет ли
   провайдер заглушку;
2. пингует полученный адрес и замеряет подключение к порту 443 (сайт может
   не отвечать на пинг, но работать);
3. выясняет, чей это адрес и какие ещё домены на нём живут: обратное имя
   (PTR), имена из сертификата сайта, владелец сети и — по желанию — внешний
   сервис со списком соседей.

Здесь нет Qt и нет виджетов: только сеть и данные. Вся сеть вызывается через
имена этого модуля, поэтому в тестах она подменяется целиком.
"""

from __future__ import annotations

import re
import socket
import time
from diagnostics.levels import FAIL, OK, UNKNOWN, WARN, worst
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from ipaddress import ip_address

from diagnostics.path_trace import FilterFacts, RouteTrace, locate_filter, trace_route
from diagnostics.quic_probe import QUIC_BLOCKED_BY_NAME, QuicVerdict
from diagnostics.quic_probe import collect as collect_quic
from diagnostics.quic_probe import judge as judge_quic
from utils.address_kinds import AddressKind, address_kind, block_stub_owner
from utils.cert_names import fetch_cert_names
from utils.dns_interception import canary_answered
from utils.dns_wire import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_NXDOMAIN,
    STATUS_OK,
    STATUS_TIMEOUT,
    TYPE_A,
    TYPE_AAAA,
    TYPE_CNAME,
    TYPE_PTR,
    TYPE_TXT,
    failure_text,
    query_doh,
    query_server,
    query_tcp,
    reverse_name,
)
from utils.ip_owner import lookup_ip_owner
from utils.socket_cancel import SocketCancel

KIND_DOMAIN = "domain"
KIND_IP = "ip"
KIND_INVALID = "invalid"

# Уровни строк — общие для всех проверок (diagnostics.levels).
LEVEL_OK = OK
LEVEL_WARN = WARN
LEVEL_FAIL = FAIL
LEVEL_UNKNOWN = UNKNOWN

SERVER_WINDOWS = "windows"
SERVER_SYSTEM = "system"
SERVER_PROVIDER = "provider"
SERVER_CUSTOM = "custom"
SERVER_DOH = "doh"

SOURCE_PTR = "ptr"
SOURCE_CERT_NAMED = "cert_named"
SOURCE_CERT_DEFAULT = "cert_default"
SOURCE_THC = "thc"
SOURCE_SHODAN = "shodan"
SOURCE_HACKERTARGET = "hackertarget"
EXTERNAL_SOURCES = (SOURCE_THC, SOURCE_HACKERTARGET, SOURCE_SHODAN)

SOURCE_OK = "ok"
SOURCE_EMPTY = "empty"
SOURCE_ERROR = "error"
SOURCE_LIMIT = "limit"
SOURCE_SKIPPED = "skipped"

RUN_DEADLINE_S = 40.0
DNS_TIMEOUT_S = 2.0
PING_COUNT = 4
PING_TIMEOUT_MS = 2000
TCP_TIMEOUT_S = 3.0
EXTERNAL_TIMEOUT_S = 8.0
# Дальше этого узла место фильтра не ищем: он стоит в сети провайдера, близко.
PATH_MAX_TTL = 20
MAX_PARALLEL = 16
# Независимые открытые DNS-серверы в дополнение к списку программы: чем больше
# разных владельцев, тем виднее, кто отвечает не как все. Mullvad сюда не годится:
# обычным запросом он называет только адреса собственных серверов, а на остальные
# имена отвечает отказом (проверено 2026-10-13). UltraDNS уже есть в списке программы.
EXTRA_SERVERS: tuple[tuple[str, str], ...] = (
    ("Яндекс DNS", "77.88.8.8"),
    ("Level3", "4.2.2.2"),
    ("Hurricane Electric", "74.82.42.42"),
    ("DNS.Watch", "84.200.69.80"),
    ("Control D", "76.76.2.0"),
    ("Comodo", "8.26.56.26"),
)
# Запасные серверы для служебных запросов (обратное имя, владелец сети).
_HELPER_SERVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
_THC_URL = "https://ip.thc.org/api/v1/lookup"
_THC_PAGE_SIZE = 100
_THC_MAX_PAGES = 3
_SHODAN_URL = "https://internetdb.shodan.io/{ip}"
_HACKERTARGET_URL = "https://api.hackertarget.com/reverseiplookup/?q={ip}"
_RIPESTAT_URL = "https://stat.ripe.net/data/prefix-overview/data.json?resource={ip}&sourceapp=zapretgui"
_USER_AGENT = "ZapretGUI domain lookup"
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


@dataclass(frozen=True, slots=True)
class DnsServer:
    label: str
    address: str
    kind: str = SERVER_PROVIDER


@dataclass(frozen=True, slots=True)
class ResolverAnswer:
    """Что ответил один DNS-сервер."""

    server: DnsServer
    status: str
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()
    cnames: tuple[str, ...] = ()
    elapsed_ms: float | None = None
    level: str = LEVEL_OK
    note: str = ""
    # Сколько секунд ответ считается свежим (наименьшее из адресных записей).
    # Настоящий сервер отдаёт убывающий остаток, заглушки часто ставят ноль
    # или круглое число. None — в ответе адресов нет.
    ttl: int | None = None
    # «Домена нет» с подписью подставного ответа (``DnsQueryResult.forged_denial``).
    forged: bool = False
    # Что тот же сервер ответил по TCP, когда на обычный запрос (UDP) пришло «домена нет».
    # Фильтр подделывает только UDP, поэтому адреса здесь — прямое доказательство подмены.
    tcp_ipv4: tuple[str, ...] = ()
    # Только у строки Windows: адрес, который система ставит первым, — на него программы идут сначала.
    first: str = ""

    @property
    def addresses(self) -> tuple[str, ...]:
        return self.ipv4 + self.ipv6


@dataclass(frozen=True, slots=True)
class PingReport:
    ip: str
    supported: bool = True
    sent: int = 0
    received: int = 0
    min_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    ttl: int | None = None
    error_code: str = ""

    @property
    def lost_percent(self) -> int:
        if self.sent <= 0:
            return 0
        return round(100 * (self.sent - self.received) / self.sent)


@dataclass(frozen=True, slots=True)
class TcpReport:
    ip: str
    port: int
    status: str  # ok / refused / timeout / error
    elapsed_ms: float | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ProgramRoad:
    """Дорога, которой на самом деле идут программы, когда Windows ставит первым адрес IPv6.

    Сайт проверяется по IPv4, а Windows при живом IPv6 ведёт программы на адрес IPv6. Если по
    нему связи нет или имя сайта на этой дороге режут, сайт «открывается» в проверке и не
    открывается в браузере — пока эту дорогу не проверить отдельно.
    """

    ip: str
    tcp: TcpReport
    # Пробы с настоящим именем сайта по этому адресу: (название, состояние ok/fail/unknown, текст).
    lines: tuple[tuple[str, str, str], ...] = ()
    # Дошло ли до сервера приветствие с именем сайта (обычное или как у Chrome). None — не проверяли.
    opens: bool | None = None


@dataclass(frozen=True, slots=True)
class NetworkInfo:
    """Чья это сеть: номер автономной системы, диапазон, страна, владелец."""

    asn: str = ""
    prefix: str = ""
    country: str = ""
    owner: str = ""
    # Все сети, которые объявляют этот адрес: (номер, владелец). Их бывает несколько.
    origins: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class NeighborSource:
    """Один источник сведений «какие домены на этом адресе»."""

    key: str
    status: str
    names: tuple[str, ...] = ()
    detail: str = ""
    # Сколько доменов знает сам источник (он отдаёт только первую часть списка).
    total: int | None = None
    # Дополнительная строка от источника, например открытые порты.
    extra: str = ""


@dataclass(frozen=True, slots=True)
class DomainLookupReport:
    target: str
    kind: str
    error: str = ""
    answers: tuple[ResolverAnswer, ...] = ()
    intercepted: bool = False
    primary_ip: str = ""
    ping: PingReport | None = None
    ping6: PingReport | None = None
    tcp: TcpReport | None = None
    # Адрес IPv6, который Windows отдаёт программам первым, и что по нему получается.
    road: ProgramRoad | None = None
    # Сервис «Редактора hosts», тумблер которого даёт этому имени адрес IPv4 (пусто — такого нет).
    hosts_service: str = ""
    # Адреса имени в файле hosts: все и те из них, что записала программа (в своём блоке).
    hosts_rows: tuple[str, ...] = ()
    hosts_managed: tuple[str, ...] = ()
    # Адреса IPv4, которые этот сервис записал бы имени.
    hosts_offered: tuple[str, ...] = ()
    # Что получается по адресу из «Редактора hosts», пока его тумблер выключен. None — не пробовали.
    hosts_road: ProgramRoad | None = None
    network: NetworkInfo | None = None
    sources: tuple[NeighborSource, ...] = ()
    # Путь до адреса по узлам. None — ещё не готов.
    route: RouteTrace | None = None
    # Проходит ли QUIC к этому сайту (только для доменов).
    quic: QuicVerdict | None = None
    # Где стоит фильтр; ищется, только если QUIC блокируют по имени.
    filter_facts: FilterFacts | None = None
    # Как сайт открывается: TLS 1.2 и 1.3, «как Chrome», HTTP, QUIC, способ блокировки, реестр РКН.
    # Та же запись сервиса, что в отчёте BlockCheck (``diagnostics.engine.check_site``); только для доменов.
    site: dict | None = None
    # Запись этой проверки в истории вкладки — та самая, что сохранена в настройки (с путём
    # к файлу полного текста). None — проверку прервали, в историю она не попала.
    history_entry: dict | None = None
    finished: bool = False
    stopped: bool = False
    timed_out: bool = False
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Разбор ввода и оценка адресов
# ---------------------------------------------------------------------------


def normalize_target(value: str) -> tuple[str, str, str]:
    """Ввод пользователя → (вид, очищенное значение, текст ошибки).

    Принимает домен, адрес, ссылку (``https://site.ru/page``) и ``host:port``.
    """
    raw = str(value or "").strip()
    if not raw:
        return KIND_INVALID, "", "Введите домен или IP-адрес."
    raw = _SCHEME_RE.sub("", raw)
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]

    try:
        return KIND_IP, str(ip_address(raw)), ""
    except ValueError:
        pass

    if raw.count(":") == 1:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            raw = host
            try:
                return KIND_IP, str(ip_address(raw)), ""
            except ValueError:
                pass

    host = raw.rstrip(".").lower()
    try:
        ascii_host = host.encode("idna").decode("ascii") if host else ""
    except UnicodeError:
        ascii_host = ""
    if not ascii_host or not _HOSTNAME_RE.match(ascii_host):
        return KIND_INVALID, "", "Не похоже на домен или IP-адрес. Пример: example.com или 1.2.3.4"
    return KIND_DOMAIN, host, ""


_KIND_NOTES: dict[AddressKind, tuple[str, str]] = {
    AddressKind.PUBLIC: (LEVEL_OK, ""),
    AddressKind.FAKE_IP: (LEVEL_WARN, "адрес VPN-клиента (fake-ip)"),
    AddressKind.SELF: (LEVEL_FAIL, "заглушка (адрес самого компьютера)"),
    AddressKind.LOCAL: (LEVEL_WARN, "локальный адрес"),
    AddressKind.CARRIER: (LEVEL_WARN, "адрес внутренней сети провайдера"),
    AddressKind.SERVICE: (LEVEL_WARN, "служебный адрес"),
    AddressKind.INVALID: (LEVEL_UNKNOWN, "непонятный адрес"),
}


def classify_ip(ip: str) -> tuple[str, str]:
    """Оценка адреса из DNS-ответа: (уровень, пояснение). Обычный адрес — ("ok", "")."""
    kind = address_kind(ip)
    if kind == AddressKind.BLOCK_STUB:
        return LEVEL_FAIL, f"заглушка ({block_stub_owner(ip)})"
    return _KIND_NOTES[kind]


def pick_ipv6(answers: Iterable[ResolverAnswer], primary_ip: str) -> str:
    """IPv6-адрес домена для отдельного пинга (если основной адрес — IPv4)."""
    for item in answers:
        for ip in item.ipv6:
            if ip != primary_ip and classify_ip(ip)[0] == LEVEL_OK:
                return ip
    return ""


def is_public_ip(ip: str) -> bool:
    try:
        return bool(ip_address(ip).is_global)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Отдельные проверки (каждая сама держит свой таймаут)
# ---------------------------------------------------------------------------


def _ask_server(server: DnsServer, domain: str) -> ResolverAnswer:
    if server.kind == SERVER_DOH:
        first = query_doh(server.address, domain, TYPE_A)
    else:
        first = query_server(server.address, domain, TYPE_A, timeout_s=DNS_TIMEOUT_S, attempts=2)
    if not first.answered:
        return ResolverAnswer(server=server, status=first.status, level=LEVEL_UNKNOWN, note=failure_text(first))
    if server.kind != SERVER_DOH and first.status == STATUS_NXDOMAIN:
        # «Домена нет» перепроверяется у того же сервера по TCP: подставной ответ приходит только по UDP.
        return ResolverAnswer(
            server=server,
            status=STATUS_NXDOMAIN,
            elapsed_ms=first.elapsed_ms,
            forged=bool(getattr(first, "forged_denial", False)),
            tcp_ipv4=query_tcp(server.address, domain, TYPE_A, timeout_s=DNS_TIMEOUT_S).values(TYPE_A),
        )
    if server.kind == SERVER_DOH:
        second = query_doh(server.address, domain, TYPE_AAAA)
    else:
        second = query_server(server.address, domain, TYPE_AAAA, timeout_s=DNS_TIMEOUT_S, attempts=1)
    ipv4 = first.values(TYPE_A)
    ipv6 = second.values(TYPE_AAAA)
    cnames = first.values(TYPE_CNAME) or second.values(TYPE_CNAME)
    if ipv4 or ipv6:
        status = STATUS_OK
    elif first.status == STATUS_OK:
        # Ответ есть, но адресов в нём нет (например, одни псевдонимы).
        status = STATUS_EMPTY
    else:
        status = first.status
    ttls = [
        record.ttl
        for result, rtype in ((first, TYPE_A), (second, TYPE_AAAA))
        for record in result.records
        if record.rtype == rtype
    ]
    return ResolverAnswer(
        server=server,
        status=status,
        ipv4=ipv4,
        ipv6=ipv6,
        cnames=cnames,
        elapsed_ms=first.elapsed_ms,
        ttl=min(ttls) if ttls else None,
    )


def _ask_windows(domain: str) -> ResolverAnswer:
    """Как имя видят обычные программы: с учётом файла hosts и шифрованного DNS Windows.

    Важно не только «адреса нет», но и почему: «такого имени нет» — это ответ DNS, которому
    Windows поверила, а молчание — DNS просто не ответил.
    """
    from utils.net_resolve import DNSTimeoutError, resolve_addrinfo

    server = DnsServer(label="Windows", address="", kind=SERVER_WINDOWS)
    started = time.perf_counter()
    status = STATUS_ERROR
    infos: list[tuple] = []
    try:
        infos = resolve_addrinfo(domain, timeout=DNS_TIMEOUT_S * 2)
    except DNSTimeoutError:
        status = STATUS_TIMEOUT
    except socket.gaierror as exc:
        # Система получила от DNS прямой ответ «имени нет» или «адресов нет». В Windows это коды
        # 11001 (WSAHOST_NOT_FOUND) и 11004 (WSANO_DATA); названия EAI_* там равны им не все.
        missing = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME), 11001, 11004}
        status = STATUS_NXDOMAIN if exc.errno in missing else STATUS_ERROR
    except Exception:
        status = STATUS_ERROR
    elapsed = (time.perf_counter() - started) * 1000.0
    ipv4 = tuple(dict.fromkeys(info[4][0] for info in infos if info[0] == socket.AF_INET and info[4]))
    ipv6 = tuple(dict.fromkeys(info[4][0] for info in infos if info[0] == socket.AF_INET6 and info[4]))
    if not ipv4 and not ipv6:
        return ResolverAnswer(server=server, status=status, level=LEVEL_UNKNOWN, elapsed_ms=elapsed)
    first = next((str(info[4][0]) for info in infos if info[4]), "")
    return ResolverAnswer(server=server, status=STATUS_OK, ipv4=ipv4, ipv6=ipv6, elapsed_ms=elapsed, first=first)


def _ping(ip: str, should_stop: Callable[[], bool]) -> PingReport:
    from utils.windows_icmp import ping_ipv4_host_winapi, ping_ipv6_winapi

    if ip_address(ip).version == 6:
        result = ping_ipv6_winapi(ip, count=PING_COUNT, timeout_ms=PING_TIMEOUT_MS, cancelled=should_stop)
    else:
        result = ping_ipv4_host_winapi(
            ip,
            count=PING_COUNT,
            timeout_ms=PING_TIMEOUT_MS,
            resolved_ip=ip,
            cancelled=should_stop,
        )
    if result.error_code == "UNSUPPORTED":
        return PingReport(ip=ip, supported=False, error_code="UNSUPPORTED")
    return PingReport(
        ip=ip,
        sent=int(result.sent),
        received=int(result.received),
        min_ms=result.min_ms,
        avg_ms=result.average_ms,
        max_ms=result.max_ms,
        ttl=result.ttl,
        error_code=str(result.error_code or ""),
    )


def _tcp_connect(ip: str, port: int = 443) -> TcpReport:
    family = socket.AF_INET6 if ip_address(ip).version == 6 else socket.AF_INET
    started = time.perf_counter()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(TCP_TIMEOUT_S)
            sock.connect((ip, port))
    except socket.timeout:
        return TcpReport(ip=ip, port=port, status="timeout")
    except ConnectionRefusedError:
        return TcpReport(ip=ip, port=port, status="refused")
    except OSError as exc:
        return TcpReport(ip=ip, port=port, status="error", detail=str(exc))
    return TcpReport(ip=ip, port=port, status="ok", elapsed_ms=(time.perf_counter() - started) * 1000.0)


def program_ipv6(answers: Iterable[ResolverAnswer]) -> str:
    """Адрес IPv6, на который Windows ведёт программы первым. Пусто — первым идёт IPv4."""
    for item in answers:
        if item.server.kind == SERVER_WINDOWS and item.status == STATUS_OK:
            return item.first if ":" in item.first and classify_ip(item.first)[0] == LEVEL_OK else ""
    return ""


def _program_road(domain: str | None, ip: str, cancel: SocketCancel) -> ProgramRoad:
    """Что получается по адресу, на который Windows ведёт программы: соединение и пробы с именем сайта."""
    tcp = _tcp_connect(ip)
    if tcp.status != "ok":
        # Вывод «связи нет» делается по соединению, а не по пингу (на пинг многие узлы не отвечают),
        # и только после второй попытки: одна несостоявшаяся — ещё не доказательство.
        tcp = _tcp_connect(ip)
    if tcp.status != "ok" or not domain:
        return ProgramRoad(ip=ip, tcp=tcp)
    from diagnostics import protocol_probe

    # Свой маленький пул: общий занят другими пробами, а эти три ждут друг друга.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="domain-road") as pool:
        found = protocol_probe.judge(protocol_probe.collect(domain, ip, submit=pool.submit, cancel=cancel))
    lines = tuple((line.title, line.state, line.text) for line in found)
    hello = [line for line in found if line.key in (protocol_probe.PROTO_TLS12, protocol_probe.PROTO_TLS13)]
    # «Не проходит» — только когда приветствие именно оборвали; несостоявшаяся проба ничего не доказывает.
    opens: bool | None = None
    if any(line.state == protocol_probe.STATE_OK for line in hello):
        opens = True
    elif any(line.state == protocol_probe.STATE_FAIL for line in hello):
        opens = False
    return ProgramRoad(ip=ip, tcp=tcp, lines=lines, opens=opens)


# Сколько адресов из «Редактора hosts» пробовать, пока один не откроет сайт, и пауза между попытками.
HOSTS_ROAD_TRIES = 2
HOSTS_ROAD_PAUSE_S = 1.5
# Если первая попытка шла дольше этого, фильтр молча глушит имя: вторая кончится так же, а ждать её долго.
HOSTS_ROAD_SLOW_S = 6.0
# Запас времени на эту пробу: она идёт после остальных и не должна выглядеть как «проверки не успели».
HOSTS_ROAD_BUDGET_S = 14.0


@dataclass(frozen=True, slots=True)
class HostsHelp:
    """Что о сайте знает «Редактор hosts» и что уже записано в файле."""

    service: str = ""
    offered: tuple[str, ...] = ()
    rows: tuple[str, ...] = ()
    managed: tuple[str, ...] = ()


def _hosts_facts(domain: str) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Сервис с адресом IPv4 для имени, его адреса IPv4, адреса имени в файле hosts и те из них, что в блоке программы."""
    from hosts.catalog_repository import HOSTS_PROFILE_ID
    from hosts.hosts import _iter_managed_hosts_block_rows, safe_read_hosts_file
    from hosts.proxy_domains import direct_service_with_ipv4, get_service_domain_ip_rows
    from utils.windows_dns_query import hosts_file_addresses

    wanted = domain.casefold()
    service = direct_service_with_ipv4(domain)
    offered = tuple(
        dict.fromkeys(
            str(address)
            for host, address in (get_service_domain_ip_rows(service, HOSTS_PROFILE_ID) if service else ())
            if str(host).casefold() == wanted and ":" not in str(address)
        )
    )
    text = safe_read_hosts_file() or ""
    managed = tuple(
        dict.fromkeys(
            str(address).strip().lower()
            for host, address in _iter_managed_hosts_block_rows(str(text).splitlines(keepends=True))
            if str(host).strip().casefold() == wanted
        )
    )
    return service, offered, tuple(address.lower() for address in hosts_file_addresses(domain)), managed


def _hosts_help(domain: str) -> HostsHelp:
    """Что «Редактор hosts» знает об имени и что уже записано в файле. В сеть не ходит."""
    try:
        service, offered, rows, managed = _hosts_facts(domain)
    except Exception:
        return HostsHelp()
    return HostsHelp(service=service, offered=offered, rows=rows, managed=managed)


def hosts_road_wanted(report: "DomainLookupReport") -> bool:
    """Стоит ли пробовать адреса из «Редактора hosts»: сайт закрыт, сервис для имени есть, его тумблер выключен."""
    site = getattr(report, "site", None) or {}
    if str(site.get("level") or "") != "fail" or not report.hosts_service or not report.hosts_offered:
        return False
    return not any(address in report.hosts_rows for address in report.hosts_offered)


def _hosts_road(domain: str, offered: tuple[str, ...], cancel: SocketCancel) -> ProgramRoad | None:
    """Адреса из «Редактора hosts» настоящим именем сайта — по очереди, до первого, по которому сайт открылся.

    Идёт после основной проверки сайта и с паузами: пачка одновременных соединений с одним именем
    сама бывает поводом для фильтра оборвать их все, и проба мешала бы основной проверке.
    """
    road: ProgramRoad | None = None
    for address in offered[:HOSTS_ROAD_TRIES]:
        if not cancel.sleep(HOSTS_ROAD_PAUSE_S):
            break
        started = time.monotonic()
        road = _program_road(domain, address, cancel)
        if road.opens or time.monotonic() - started > HOSTS_ROAD_SLOW_S:
            break
    return road


ROAD_DEAD = "dead"
ROAD_BLOCKED = "blocked"


def road_trouble(report: "DomainLookupReport") -> str:
    """Беда на дороге программ: ``ROAD_DEAD`` — по IPv6 нет связи, ``ROAD_BLOCKED`` — связь есть,
    а имя сайта не проходит. Пусто — беды нет или сравнить не с чем.

    Вывод делается только из сравнения: по IPv4 то же самое работает. Если не работает и там,
    это обычная блокировка или лежащий сайт — о них говорят другие строки.
    """
    road, tcp = getattr(report, "road", None), getattr(report, "tcp", None)
    if road is None or tcp is None or tcp.status != "ok" or ":" in tcp.ip:
        return ""
    if road.tcp.status != "ok":
        return ROAD_DEAD
    site = getattr(report, "site", None) or {}
    if road.opens is False and str(site.get("level") or "") in ("ok", "warn"):
        return ROAD_BLOCKED
    return ""


def road_is_the_only_way(report: "DomainLookupReport") -> bool:
    """Windows отдаёт программам одни адреса IPv6: перейти на IPv4 им некуда.

    Так бывает, когда в файле hosts у имени записаны только адреса IPv6: про IPv4 Windows тогда
    у DNS уже не спрашивает (проверено на стенде 2026-10-14).
    """
    for item in getattr(report, "answers", ()):
        if item.server.kind == SERVER_WINDOWS and item.status == STATUS_OK:
            return bool(item.ipv6) and not item.ipv4
    return False


def _ask_helpers(name: str, rtype: int, servers: Iterable[str]):
    """Служебный запрос: спрашиваем серверы по очереди, пока кто-то не ответит."""
    last = None
    for server in servers:
        last = query_server(server, name, rtype, timeout_s=DNS_TIMEOUT_S, attempts=1)
        if last.answered:
            return last
    return last


def _lookup_ptr(ip: str, servers: tuple[str, ...]) -> NeighborSource:
    result = _ask_helpers(reverse_name(ip), TYPE_PTR, servers)
    if result is None or not result.answered:
        return NeighborSource(key=SOURCE_PTR, status=SOURCE_ERROR, detail="DNS-серверы не ответили")
    names = tuple(name.rstrip(".") for name in result.values(TYPE_PTR))
    return NeighborSource(key=SOURCE_PTR, status=SOURCE_OK if names else SOURCE_EMPTY, names=names)


def _lookup_network(ip: str, servers: tuple[str, ...]) -> NetworkInfo | None:
    """Владелец сети через DNS-службу Team Cymru: без сайтов и ключей, одним DNS-запросом."""
    found = lookup_ip_owner(ip, lambda name, rtype: _ask_helpers(name, rtype, servers))
    if found is None:
        return None
    return NetworkInfo(asn=found.asn, prefix=found.prefix, country=found.country, owner=found.owner)


def _lookup_cert(ip: str, key: str, server_name: str | None, site_opens: bool = False) -> NeighborSource:
    result = fetch_cert_names(ip, server_name=server_name)
    if not result.ok:
        if result.kind == "tls" and server_name is None:
            # Сети доставки (Cloudflare и подобные) без имени сайта сертификат не отдают.
            return NeighborSource(key=key, status=SOURCE_SKIPPED, detail="сервер не отдаёт сертификат без имени сайта")
        if server_name is not None and result.kind in ("timeout", "tls", "error"):
            # Порт открыт, а защищённое соединение с именем сайта не складывается. Про блокировку по
            # имени можно говорить, только если сам сайт не открылся: иначе это сбой этого запроса.
            if site_opens:
                detail = f"сертификат с именем {server_name} прочитать не удалось ({result.detail}); сам сайт при этом открывается"
            else:
                detail = f"соединение с именем {server_name} не установилось — так бывает, когда сайт блокируют по имени"
            return NeighborSource(key=key, status=SOURCE_ERROR, detail=detail)
        return NeighborSource(key=key, status=SOURCE_ERROR, detail=result.detail)
    names = list(result.names)
    if result.common_name and result.common_name.lower() not in names:
        names.insert(0, result.common_name.lower())
    placeholders = [name for name in names if _is_placeholder_name(name)]
    names = [name for name in names if name not in placeholders]
    if placeholders and not names:
        # Google и подобные на запрос без имени сайта отдают сертификат на несуществующее имя.
        return NeighborSource(
            key=key,
            status=SOURCE_SKIPPED,
            detail=f"без имени сайта сервер отдаёт сертификат-пустышку ({placeholders[0]}), имён сайтов в нём нет",
        )
    return NeighborSource(key=key, status=SOURCE_OK if names else SOURCE_EMPTY, names=tuple(names))


def _is_placeholder_name(name: str) -> bool:
    """Имя из зоны, которой в интернете нет по стандарту (RFC 6761): это не сайт, а заглушка в сертификате."""
    return name.rstrip(".").lower().rsplit(".", 1)[-1] in ("invalid", "localhost")


def _http(method: str, url: str, **kwargs):
    """Запрос к внешнему сервису напрямую, мимо системного прокси (его может выставить winws)."""
    import requests

    from utils.https_dns_fallback import request_with_dns_fallback

    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    headers = {"User-Agent": _USER_AGENT, **kwargs.pop("headers", {})}
    try:
        response = request_with_dns_fallback(
            session, method, url, timeout=EXTERNAL_TIMEOUT_S, headers=headers, **kwargs
        )
        return int(response.status_code), str(response.text or "")
    finally:
        session.close()


def _unavailable(key: str, exc: BaseException) -> NeighborSource:
    return NeighborSource(key=key, status=SOURCE_ERROR, detail=_unavailable_text(exc))


def _unavailable_text(exc: BaseException) -> str:
    """Почему внешний сервис не ответил — словами, а не названием ошибки."""
    import requests

    if isinstance(exc, requests.exceptions.Timeout):
        return "сервис не ответил вовремя"
    if isinstance(exc, requests.exceptions.SSLError):
        return "защищённое соединение с сервисом не установилось"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "с сервисом не удалось соединиться — возможно, он закрыт для вашей сети"
    return "сервис недоступен"


def _clean_names(values: Iterable[object]) -> tuple[str, ...]:
    names: list[str] = []
    for value in values:
        name = str(value or "").strip().rstrip(".").lower()
        if _HOSTNAME_RE.match(name) and name not in names:
            names.append(name)
    return tuple(names)


def parse_thc_answer(status_code: int, text: str) -> tuple[NeighborSource, str]:
    """Ответ ip.thc.org → (источник, метка следующей страницы)."""
    import json

    if status_code == 429:
        return NeighborSource(key=SOURCE_THC, status=SOURCE_LIMIT, detail="слишком много запросов, попробуйте позже"), ""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if status_code != 200 or not isinstance(data, dict):
        return NeighborSource(key=SOURCE_THC, status=SOURCE_ERROR, detail=f"сервис ответил кодом {status_code}"), ""
    if data.get("status") == "error":
        detail = str(data.get("error") or data.get("message") or "ошибка сервиса")
        return NeighborSource(key=SOURCE_THC, status=SOURCE_ERROR, detail=detail[:120]), ""
    rows = data.get("domains") or ()
    names = _clean_names(row.get("domain") for row in rows if isinstance(row, dict))
    total = data.get("matching_records")
    if data.get("count_unavailable") or not isinstance(total, int) or total < len(names):
        total = None
    status = SOURCE_OK if names else SOURCE_EMPTY
    return NeighborSource(key=SOURCE_THC, status=status, names=names, total=total), str(data.get("next_page_state") or "")


def _lookup_thc(ip: str, halted: Callable[[], bool]) -> NeighborSource:
    """Домены на адресе по базе ip.thc.org: без ключа, до трёх страниц по сто записей."""
    source: NeighborSource | None = None
    page_state = ""
    for _page in range(_THC_MAX_PAGES):
        body: dict[str, object] = {"ip_address": ip, "limit": _THC_PAGE_SIZE}
        if page_state:
            body["page_state"] = page_state
        try:
            status_code, text = _http("POST", _THC_URL, json=body)
        except Exception as exc:
            return source if source is not None else _unavailable(SOURCE_THC, exc)
        page, page_state = parse_thc_answer(status_code, text)
        if source is None:
            source = page
        elif page.status == SOURCE_OK:
            source = replace(source, names=tuple(dict.fromkeys(source.names + page.names)))
        if page.status != SOURCE_OK or not page_state or halted():
            break
    if source is None:
        return NeighborSource(key=SOURCE_THC, status=SOURCE_EMPTY)
    if not page_state:
        # Список получен целиком: «всего в базе» больше не нужно (там считаются и повторы).
        source = replace(source, total=None)
    return source


def parse_hackertarget_answer(status_code: int, text: str) -> NeighborSource:
    """Ответ HackerTarget: по домену в строке либо строка с отказом (код при этом 200)."""
    body = str(text or "").strip()
    lowered = body.lower()
    if status_code == 429 or "api count exceeded" in lowered:
        return NeighborSource(
            key=SOURCE_HACKERTARGET, status=SOURCE_LIMIT, detail="исчерпан дневной лимит бесплатных запросов"
        )
    if status_code != 200:
        return NeighborSource(key=SOURCE_HACKERTARGET, status=SOURCE_ERROR, detail=f"сервис ответил кодом {status_code}")
    names = _clean_names(body.splitlines())
    if names:
        return NeighborSource(key=SOURCE_HACKERTARGET, status=SOURCE_OK, names=names)
    if not body or "no dns a records" in lowered or "no records" in lowered:
        return NeighborSource(key=SOURCE_HACKERTARGET, status=SOURCE_EMPTY)
    return NeighborSource(key=SOURCE_HACKERTARGET, status=SOURCE_ERROR, detail=body[:120])


def _lookup_hackertarget(ip: str) -> NeighborSource:
    try:
        status_code, text = _http("GET", _HACKERTARGET_URL.format(ip=ip))
    except Exception as exc:
        return _unavailable(SOURCE_HACKERTARGET, exc)
    return parse_hackertarget_answer(status_code, text)


def _lookup_neighbors(ip: str, halted: Callable[[], bool]) -> tuple[NeighborSource, ...]:
    """Основной источник — THC. HackerTarget (всего ~20 запросов в сутки) — только если THC не помог."""
    primary = _lookup_thc(ip, halted)
    if primary.status == SOURCE_OK or halted():
        return (primary,)
    return (primary, _lookup_hackertarget(ip))


def parse_shodan_answer(status_code: int, text: str) -> NeighborSource:
    import json

    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if status_code == 404 or (isinstance(data, dict) and "detail" in data and "hostnames" not in data):
        return NeighborSource(key=SOURCE_SHODAN, status=SOURCE_EMPTY)
    if status_code == 429:
        return NeighborSource(key=SOURCE_SHODAN, status=SOURCE_LIMIT, detail="слишком много запросов, попробуйте позже")
    if status_code != 200 or not isinstance(data, dict):
        return NeighborSource(key=SOURCE_SHODAN, status=SOURCE_ERROR, detail=f"сервис ответил кодом {status_code}")
    names = _clean_names(data.get("hostnames") or ())
    ports = [str(port) for port in (data.get("ports") or ()) if isinstance(port, int)]
    extra = "открытые порты: " + ", ".join(ports[:40]) if ports else ""
    status = SOURCE_OK if names or extra else SOURCE_EMPTY
    return NeighborSource(key=SOURCE_SHODAN, status=status, names=names, extra=extra)


def _lookup_shodan(ip: str) -> NeighborSource:
    try:
        status_code, text = _http("GET", _SHODAN_URL.format(ip=ip))
    except Exception as exc:
        return _unavailable(SOURCE_SHODAN, exc)
    return parse_shodan_answer(status_code, text)


def parse_ripestat_answer(status_code: int, text: str) -> NetworkInfo | None:
    """Официальные данные RIPE: подсеть и все сети, которые её объявляют."""
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return None
    if status_code != 200 or not isinstance(data, dict):
        return None
    payload = data.get("data")
    if not isinstance(payload, dict):
        return None
    origins: list[tuple[str, str]] = []
    for item in payload.get("asns") or ():
        if isinstance(item, dict) and item.get("asn") is not None:
            origins.append((str(item.get("asn")), str(item.get("holder") or "").strip()))
    prefix = str(payload.get("resource") or "") if payload.get("announced") else ""
    if not origins and not prefix:
        return None
    return NetworkInfo(prefix=prefix, origins=tuple(origins))


def _lookup_ripestat(ip: str) -> NetworkInfo | None:
    try:
        status_code, text = _http("GET", _RIPESTAT_URL.format(ip=ip))
    except Exception:
        return None
    return parse_ripestat_answer(status_code, text)


def merge_network(current: NetworkInfo | None, update: NetworkInfo | None) -> NetworkInfo | None:
    """Склеивает сведения о сети из двух источников: непустое поле не затирается пустым."""
    if current is None or update is None:
        return current or update
    return NetworkInfo(
        asn=current.asn or update.asn,
        prefix=current.prefix or update.prefix,
        country=current.country or update.country,
        owner=current.owner or update.owner,
        origins=current.origins or update.origins,
    )


# ---------------------------------------------------------------------------
# Сведение ответов
# ---------------------------------------------------------------------------


def annotate_answers(answers: Iterable[ResolverAnswer]) -> tuple[ResolverAnswer, ...]:
    """Проставляет уровень и пояснение каждому ответу.

    Разные адреса у разных серверов — норма (у крупных сайтов серверы по всему
    миру), поэтому помечаются только явные признаки: заглушка, локальный адрес,
    «домена нет» там, где у других он есть.
    """
    items = list(answers)
    someone_resolved = any(
        item.status == STATUS_OK and item.addresses for item in items if item.server.kind != SERVER_WINDOWS
    )
    forged = {id(item) for item in forged_answers(items)}
    result: list[ResolverAnswer] = []
    for item in items:
        level, note = LEVEL_OK, ""
        if item.server.kind == SERVER_WINDOWS and item.status != STATUS_OK:
            # Строка Windows — то, что получают браузер и программы. Если серверы адрес
            # знают, а Windows нет, это главная находка, а не «не узнали».
            if someone_resolved:
                level, note = LEVEL_WARN, NOTE_WINDOWS_BLIND
            else:
                level = LEVEL_UNKNOWN
        elif id(item) in forged:
            level, note = LEVEL_WARN, _forged_note(item, items)
        elif item.status == STATUS_OK:
            notes: list[str] = []
            for ip in item.addresses:
                ip_level, ip_note = classify_ip(ip)
                level = worst((level, ip_level))
                if ip_note and ip_note not in notes:
                    notes.append(ip_note)
            note = ", ".join(notes)
        elif item.status in (STATUS_NXDOMAIN, STATUS_EMPTY):
            if someone_resolved:
                level = LEVEL_WARN
                note = "у других серверов адрес есть"
            else:
                level = LEVEL_UNKNOWN
        else:
            # Отказ сервера или сбой запроса: о самом адресе это ничего не говорит.
            level, note = LEVEL_UNKNOWN, item.note
        result.append(replace(item, level=level, note=note))
    return tuple(result)


NOTE_WINDOWS_BLIND = "у DNS-серверов адрес есть, а Windows его не получила — браузер и программы сайт не найдут"
NOTE_FORGED = "тот же сервер по шифрованному каналу адрес называет — обычный ответ подменён по дороге"
NOTE_FORGED_MARK = (
    "ответ подставной: настоящий сервер так не отвечает (в отказе нет обязательной служебной записи) — "
    "его прислал фильтр по дороге"
)


NOTE_FORGED_TCP = "по TCP тот же сервер адрес называет ({ip}) — ответ на обычный запрос подменён по дороге"


def _forged_note(item: ResolverAnswer, answers: Iterable[ResolverAnswer]) -> str:
    """Чем доказана подмена — самым прямым из сработавших признаков."""
    if item.tcp_ipv4:
        return NOTE_FORGED_TCP.format(ip=item.tcp_ipv4[0])
    if item.server.address in encrypted_known(answers):
        return NOTE_FORGED
    return NOTE_FORGED_MARK


def encrypted_known(answers: Iterable[ResolverAnswer]) -> set[str]:
    """Адреса серверов, которые по шифрованному каналу назвали адрес сайта."""
    return {
        item.server.address
        for item in answers
        if item.server.kind == SERVER_DOH and item.status == STATUS_OK and item.addresses
    }


def forged_answers(answers: Iterable[ResolverAnswer]) -> tuple[ResolverAnswer, ...]:
    """Подставные ответы «домена нет» на обычный запрос. Три независимых признака.

    Первый и второй — сравнение сервера с самим собой: по TCP или по шифрованному
    каналу он адрес назвал, а на обычный запрос (UDP) «ответил», что домена нет.
    Настоящий сервер так не может, значит обычный ответ прислал кто-то по дороге.

    Третий — подпись самого ответа (``ResolverAnswer.forged``). Она встречается и
    у домашних фильтров рекламы, поэтому учитывается только когда другие серверы
    адрес назвали: тогда ясно, что сайт существует, а отказ придуман.
    """
    items = list(answers)
    known = encrypted_known(items)
    someone_resolved = any(
        item.status == STATUS_OK and item.addresses for item in items if item.server.kind != SERVER_WINDOWS
    )
    return tuple(
        item
        for item in items
        if item.server.kind not in (SERVER_DOH, SERVER_WINDOWS)
        and (
            (item.status in (STATUS_NXDOMAIN, STATUS_EMPTY) and (item.tcp_ipv4 or item.server.address in known))
            or (item.status == STATUS_NXDOMAIN and item.forged and someone_resolved)
        )
    )


def windows_blind(answers: Iterable[ResolverAnswer]) -> bool:
    """Windows адрес не получила, хотя DNS-серверы его знают: программы на компьютере сайт не найдут."""
    items = list(answers)
    windows = [item for item in items if item.server.kind == SERVER_WINDOWS]
    others = [item for item in items if item.server.kind != SERVER_WINDOWS]
    return bool(
        windows
        and all(item.status != STATUS_OK for item in windows)
        and any(item.status == STATUS_OK and item.addresses for item in others)
    )


def pick_primary_ip(answers: Iterable[ResolverAnswer]) -> str:
    """Адрес для пинга и поиска соседей.

    Сначала тот, которым реально пользуются программы на компьютере (строка
    Windows), затем ответ DNS-серверов системы, затем шифрованный ответ (по нему
    же проверяется, как открывается сайт) и только потом самый частый среди
    остальных. Иначе у крупного сайта «самым частым» оказывается далёкий
    зарубежный узел, на который этот компьютер никогда не попадёт. Заглушки
    берём в последнюю очередь: если других адресов нет, полезно увидеть, что
    отвечает именно она.
    """
    items = list(answers)

    def clean(addresses: Iterable[str]) -> list[str]:
        return [ip for ip in addresses if classify_ip(ip)[0] == LEVEL_OK]

    for kind in (SERVER_WINDOWS, SERVER_SYSTEM, SERVER_DOH):
        for item in items:
            if item.server.kind == kind:
                # У сервера с подставным «домена нет» настоящий ответ — тот, что он дал по TCP.
                good = clean(item.ipv4) or clean(item.tcp_ipv4)
                if good:
                    return good[0]
    for family in ("ipv4", "ipv6"):
        counter: Counter[str] = Counter()
        for item in items:
            for ip in clean(getattr(item, family)):
                counter[ip] += 1
        if counter:
            return counter.most_common(1)[0][0]
    for item in items:
        if item.addresses:
            return item.addresses[0]
    return ""


# ---------------------------------------------------------------------------
# Прогон
# ---------------------------------------------------------------------------


class _Run:
    """Общий пул потоков, кнопка «Стоп» и общий предел времени на всю проверку."""

    def __init__(self, should_stop: Callable[[], bool] | None) -> None:
        self._should_stop = should_stop
        self._deadline = time.monotonic() + RUN_DEADLINE_S
        self.pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="domain-lookup")
        # Снимает пробы QUIC, когда проверку остановили или вышло время.
        self.cancel = SocketCancel()

    def stopped(self) -> bool:
        try:
            return bool(self._should_stop and self._should_stop())
        except Exception:
            return False

    def expired(self) -> bool:
        return time.monotonic() >= self._deadline

    def halted(self) -> bool:
        return self.stopped() or self.expired()

    def extend(self, seconds: float) -> None:
        """Даёт время на шаг, который идёт после всех остальных."""
        self._deadline = max(self._deadline, time.monotonic()) + max(0.0, float(seconds))

    def each_done(self, futures: dict[Future, object]):
        """Отдаёт (ключ, результат) по мере готовности; прекращает по «Стоп» и по времени."""
        pending = set(futures)
        while pending and not self.halted():
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    yield futures[future], future.result()
                except Exception:
                    yield futures[future], None

    def close(self) -> None:
        self.cancel.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)


def run_domain_lookup(
    target: str,
    *,
    servers: Iterable[DnsServer] = (),
    use_external: bool = True,
    on_stage: Callable[[DomainLookupReport], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> DomainLookupReport:
    """Проверка домена или адреса — двумя волнами.

    Первая волна — то, что делает и браузер: адрес у Windows, у DNS компьютера и у
    шифрованного эталона, затем сайт открывается так, как его открыл бы Chrome.
    Вторая волна — подробный разбор: десятки DNS-серверов, пинг, путь до сервера,
    сертификаты, сведения о сети и соседи по адресу. Она идёт всегда, даже когда
    сайт открылся и DNS честный: человек сам нажал кнопку, и ему удобнее видеть
    полную картину (решение пользователя от 2026-10-17).

    ``on_stage`` получает промежуточный отчёт каждый раз, когда готова очередная
    часть, — страница показывает результаты по мере появления.
    """
    started = time.perf_counter()
    kind, value, error = normalize_target(target)
    if kind == KIND_INVALID:
        return DomainLookupReport(target=str(target or "").strip(), kind=kind, error=error, finished=True)

    report = DomainLookupReport(target=value, kind=kind)
    server_list = _unique_servers(servers)
    run = _Run(should_stop)

    def publish(updated: DomainLookupReport) -> DomainLookupReport:
        updated = replace(updated, elapsed_s=time.perf_counter() - started)
        if on_stage is not None:
            try:
                on_stage(updated)
            except Exception:
                pass
        return updated

    quick = tuple(server for server in server_list if server.kind in QUICK_SERVER_KINDS)
    rest = tuple(server for server in server_list if server.kind not in QUICK_SERVER_KINDS)
    try:
        if kind == KIND_DOMAIN:
            report = _resolve_stage(run, report, quick, publish, canary=False)
            primary_ip = pick_primary_ip(report.answers)
        else:
            primary_ip = value
        report = publish(replace(report, primary_ip=primary_ip))

        if primary_ip and not run.halted():
            report = _browser_stage(run, report, publish)
            opened = opened_address(report.site)
            if opened and opened != report.primary_ip:
                # Пинг, путь, сертификат и сеть — по адресу, на котором сайт открылся: иначе отчёт описывает один
                # адрес, а сайт открыт на другом. Остальные адреса остаются в строке «Какие адреса пробовали».
                report = publish(replace(report, primary_ip=opened))
        if not run.halted():
            if kind == KIND_DOMAIN:
                report = _resolve_stage(run, report, rest, publish, canary=True)
            if report.primary_ip and not run.halted():
                report = _address_stage(run, report, server_list, use_external, publish)
    finally:
        run.close()

    return replace(
        report,
        finished=True,
        stopped=run.stopped(),
        timed_out=run.expired() and not run.stopped(),
        elapsed_s=time.perf_counter() - started,
    )


def _unique_servers(servers: Iterable[DnsServer]) -> tuple[DnsServer, ...]:
    seen: set[str] = set()
    result: list[DnsServer] = []
    for server in servers:
        address = str(server.address or "").strip()
        key = f"{'doh' if server.kind == SERVER_DOH else 'udp'}:{address}"
        if not address or key in seen:
            continue
        try:
            ip_address(address)
        except ValueError:
            continue
        seen.add(key)
        result.append(server)
    return tuple(result)


# Кого спрашивают всегда: Windows, DNS компьютера, свои серверы человека и шифрованный эталон —
# столько же запросов делает и сам компьютер. Остальные (провайдеры и общедоступные) — во второй волне.
QUICK_SERVER_KINDS = frozenset({SERVER_SYSTEM, SERVER_CUSTOM, SERVER_DOH})


def _resolve_stage(run: _Run, report: DomainLookupReport, servers, publish, *, canary: bool) -> DomainLookupReport:
    """Ответы DNS-серверов ``servers``; прежние ответы отчёта остаются первыми."""
    domain = report.target
    earlier = [item for item in report.answers]
    futures: dict[Future, object] = {}
    if not earlier:
        futures[run.pool.submit(_ask_windows, domain)] = -1
    for index, server in enumerate(servers):
        futures[run.pool.submit(_ask_server, server, domain)] = len(earlier) + index
    if canary:
        futures[run.pool.submit(canary_answered, domain)] = "canary"

    collected: dict[int, ResolverAnswer] = {index: item for index, item in enumerate(earlier)}
    intercepted = report.intercepted
    for key, result in run.each_done(futures):
        if key == "canary":
            intercepted = bool(result)
        elif isinstance(result, ResolverAnswer):
            collected[int(key)] = result
        else:
            continue
        ordered = [collected[index] for index in sorted(collected)]
        report = publish(replace(report, answers=annotate_answers(ordered), intercepted=intercepted))
    return report


def _inspect_quic(domain: str, ip: str, max_ttl: int, cancel: SocketCancel) -> tuple[QuicVerdict | None, FilterFacts | None]:
    """QUIC к сайту, а если его блокируют по имени — ещё и место фильтра."""
    # Свой маленький пул: общий занят другими пробами, а эти три ждут друг друга.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="domain-quic") as pool:
        verdict = judge_quic(collect_quic(domain, ip, submit=pool.submit, cancel=cancel))
    if verdict is None or verdict.code != QUIC_BLOCKED_BY_NAME:
        return verdict, None
    return verdict, locate_filter(ip, domain, max_ttl=max_ttl, cancel=cancel)


def opened_address(site: dict | None) -> str:
    """Адрес, на котором главный адрес сайта открылся. Пусто — сайт не открылся или адрес неизвестен."""
    for item in (site or {}).get("targets") or ():
        if item.get("main") and item.get("ok") and item.get("address"):
            address = str(item["address"])
            return address if ":" not in address else ""
    return ""


def _check_site(domain: str, should_stop: Callable[[], bool]) -> dict | None:
    """Сайт теми же пробами, что в BlockCheck. Движок подключается здесь: остальной проверке адреса он не нужен."""
    from diagnostics.engine import check_site

    return check_site(domain, should_stop=should_stop)


def _browser_stage(run: _Run, report: DomainLookupReport, publish) -> DomainLookupReport:
    """Первая волна по адресу: сайт открывается так, как его открыл бы Chrome, и QUIC — как у браузера.

    Разбор причины внутри обеих проб — только когда сайт не открылся (``diagnostics.engine``,
    ``diagnostics.quic_probe``).
    """
    domain = report.target if report.kind == KIND_DOMAIN else None
    if not domain or not is_public_ip(report.primary_ip):
        return report
    ip = report.primary_ip
    futures: dict[Future, object] = {
        run.pool.submit(_check_site, domain, run.halted): "site",
        run.pool.submit(_hosts_help, domain): "hosts_help",
        # Соединение с портом 443 браузер открывает и сам; по нему сравнивается дорога IPv6.
        run.pool.submit(_tcp_connect, ip): "tcp",
    }
    if ":" not in ip:
        futures[run.pool.submit(_inspect_quic, domain, ip, PATH_MAX_TTL, run.cancel)] = "quic"
    # Windows ставит первым IPv6 — по нему и пойдёт браузер: эта дорога — часть первой волны.
    road_ip = program_ipv6(report.answers) if ":" not in ip else ""
    if road_ip:
        futures[run.pool.submit(_program_road, domain, road_ip, run.cancel)] = "road"
    for key, result in run.each_done(futures):
        if key == "tcp" and isinstance(result, TcpReport):
            report = replace(report, tcp=result)
        elif key == "road" and isinstance(result, ProgramRoad):
            report = replace(report, road=result)
        elif key == "site" and isinstance(result, dict):
            report = replace(report, site=result)
        elif key == "quic" and isinstance(result, tuple):
            report = replace(report, quic=result[0], filter_facts=result[1])
        elif key == "hosts_help" and isinstance(result, HostsHelp):
            report = replace(
                report,
                hosts_service=result.service,
                hosts_rows=result.rows,
                hosts_managed=result.managed,
                hosts_offered=result.offered,
            )
        else:
            continue
        report = publish(report)
    return report


def _address_stage(run: _Run, report: DomainLookupReport, servers, use_external: bool, publish) -> DomainLookupReport:
    """Вторая волна по адресу: пинг, путь, сертификаты, сеть и соседи (сайт и QUIC уже проверены)."""
    ip = report.primary_ip
    public = is_public_ip(ip)
    helpers = tuple(
        dict.fromkeys(
            [server.address for server in servers if server.kind == SERVER_SYSTEM] + list(_HELPER_SERVERS)
        )
    )
    domain = report.target if report.kind == KIND_DOMAIN else None

    order = [SOURCE_PTR]
    futures: dict[Future, object] = {
        run.pool.submit(_ping, ip, run.halted): "ping",
        run.pool.submit(_lookup_ptr, ip, helpers): SOURCE_PTR,
    }
    if report.tcp is None:
        futures[run.pool.submit(_tcp_connect, ip)] = "tcp"
    # Пингуется тот IPv6, на который Windows ведёт программы; нет такого — любой адрес IPv6 сайта.
    road_ip = program_ipv6(report.answers) if ":" not in ip else ""
    ipv6 = road_ip or pick_ipv6(report.answers, ip)
    if ipv6:
        futures[run.pool.submit(_ping, ipv6, run.halted)] = "ping6"
    if road_ip and report.road is None:
        futures[run.pool.submit(_program_road, domain, road_ip, run.cancel)] = "road"
    if ":" not in ip:
        futures[run.pool.submit(trace_route, ip, should_stop=run.halted)] = "route"
    if domain:
        order.append(SOURCE_CERT_NAMED)
        site_opens = str((report.site or {}).get("level") or "") in ("ok", "warn")
        futures[run.pool.submit(_lookup_cert, ip, SOURCE_CERT_NAMED, domain, site_opens)] = SOURCE_CERT_NAMED
    order.append(SOURCE_CERT_DEFAULT)
    futures[run.pool.submit(_lookup_cert, ip, SOURCE_CERT_DEFAULT, None)] = SOURCE_CERT_DEFAULT
    order.extend(EXTERNAL_SOURCES)

    sources: dict[str, NeighborSource] = {}
    if public:
        futures[run.pool.submit(_lookup_network, ip, helpers)] = "network"
    skip_reason = "" if use_external else "выключено"
    if not public:
        skip_reason = "локальный адрес наружу не отправляется"
    if skip_reason:
        for key in (SOURCE_THC, SOURCE_SHODAN):
            sources[key] = NeighborSource(key=key, status=SOURCE_SKIPPED, detail=skip_reason)
    else:
        futures[run.pool.submit(_lookup_neighbors, ip, run.halted)] = "neighbors"
        futures[run.pool.submit(_lookup_shodan, ip)] = SOURCE_SHODAN
        futures[run.pool.submit(_lookup_ripestat, ip)] = "ripestat"

    def ordered_sources() -> tuple[NeighborSource, ...]:
        return tuple(sources[key] for key in order if key in sources)

    report = replace(report, sources=ordered_sources())
    for key, result in run.each_done(futures):
        if key == "ping" and isinstance(result, PingReport):
            report = replace(report, ping=result)
        elif key == "ping6" and isinstance(result, PingReport):
            report = replace(report, ping6=result)
        elif key == "tcp" and isinstance(result, TcpReport):
            report = replace(report, tcp=result)
        elif key == "road" and isinstance(result, ProgramRoad):
            report = replace(report, road=result)
        elif key == "hosts_help" and isinstance(result, HostsHelp):
            report = replace(
                report,
                hosts_service=result.service,
                hosts_rows=result.rows,
                hosts_managed=result.managed,
                hosts_offered=result.offered,
            )
        elif key == "route" and isinstance(result, RouteTrace):
            report = replace(report, route=result)
        elif key == "quic" and isinstance(result, tuple):
            report = replace(report, quic=result[0], filter_facts=result[1])
        elif key == "site":
            if not isinstance(result, dict):
                continue
            report = replace(report, site=result)
        elif key in ("network", "ripestat"):
            if not isinstance(result, NetworkInfo):
                continue
            report = replace(report, network=merge_network(report.network, result))
        elif key == "neighbors" and isinstance(result, tuple):
            for item in result:
                sources[item.key] = item
            report = replace(report, sources=ordered_sources())
        elif isinstance(result, NeighborSource):
            sources[str(key)] = result
            report = replace(report, sources=ordered_sources())
        elif key in order:
            sources[str(key)] = NeighborSource(key=str(key), status=SOURCE_ERROR, detail="внутренняя ошибка")
            report = replace(report, sources=ordered_sources())
        else:
            continue
        report = publish(report)
    if domain and hosts_road_wanted(report) and not run.halted():
        # Сайт закрыт, а у «Редактора hosts» есть для него свои адреса: откроется ли он по ним.
        run.extend(HOSTS_ROAD_BUDGET_S)
        report = publish(replace(report, hosts_road=_hosts_road(domain, report.hosts_offered, run.cancel)))
    return report


__all__ = [
    "EXTRA_SERVERS",
    "KIND_DOMAIN",
    "KIND_INVALID",
    "KIND_IP",
    "LEVEL_FAIL",
    "LEVEL_OK",
    "LEVEL_UNKNOWN",
    "LEVEL_WARN",
    "SERVER_CUSTOM",
    "SERVER_DOH",
    "SERVER_PROVIDER",
    "SERVER_SYSTEM",
    "SERVER_WINDOWS",
    "ROAD_BLOCKED",
    "ROAD_DEAD",
    "HostsHelp",
    "ProgramRoad",
    "hosts_road_wanted",
    "program_ipv6",
    "road_is_the_only_way",
    "road_trouble",
    "SOURCE_CERT_DEFAULT",
    "SOURCE_CERT_NAMED",
    "SOURCE_EMPTY",
    "SOURCE_ERROR",
    "EXTERNAL_SOURCES",
    "SOURCE_HACKERTARGET",
    "SOURCE_SHODAN",
    "SOURCE_THC",
    "SOURCE_LIMIT",
    "SOURCE_OK",
    "SOURCE_PTR",
    "SOURCE_SKIPPED",
    "DnsServer",
    "DomainLookupReport",
    "NeighborSource",
    "NetworkInfo",
    "PingReport",
    "ResolverAnswer",
    "TcpReport",
    "NOTE_FORGED",
    "NOTE_FORGED_MARK",
    "NOTE_FORGED_TCP",
    "NOTE_WINDOWS_BLIND",
    "annotate_answers",
    "classify_ip",
    "encrypted_known",
    "forged_answers",
    "is_public_ip",
    "normalize_target",
    "merge_network",
    "parse_hackertarget_answer",
    "parse_ripestat_answer",
    "parse_shodan_answer",
    "parse_thc_answer",
    "pick_ipv6",
    "pick_primary_ip",
    "run_domain_lookup",
    "windows_blind",
]
