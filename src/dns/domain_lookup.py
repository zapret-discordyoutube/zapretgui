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
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from ipaddress import ip_address

from utils.address_kinds import AddressKind, address_kind, block_stub_owner
from utils.cert_names import fetch_cert_names
from utils.dns_interception import canary_answered
from utils.dns_wire import (
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_NXDOMAIN,
    STATUS_OK,
    STATUS_REFUSED,
    STATUS_TIMEOUT,
    TYPE_A,
    TYPE_AAAA,
    TYPE_CNAME,
    TYPE_PTR,
    TYPE_TXT,
    failure_text,
    query_doh,
    query_server,
    reverse_name,
)

KIND_DOMAIN = "domain"
KIND_IP = "ip"
KIND_INVALID = "invalid"

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_FAIL = "fail"
LEVEL_UNKNOWN = "unknown"

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
MAX_PARALLEL = 16
# Независимые открытые DNS-серверы в дополнение к списку программы: чем больше
# разных владельцев, тем виднее, кто отвечает не как все.
EXTRA_SERVERS: tuple[tuple[str, str], ...] = (
    ("Яндекс DNS", "77.88.8.8"),
    ("Level3", "4.2.2.2"),
    ("Hurricane Electric", "74.82.42.42"),
    ("DNS.Watch", "84.200.69.80"),
    ("Mullvad", "194.242.2.2"),
    ("Control D", "76.76.2.0"),
    ("Comodo", "8.26.56.26"),
    ("UltraDNS", "156.154.70.1"),
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
    network: NetworkInfo | None = None
    sources: tuple[NeighborSource, ...] = ()
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
    return ResolverAnswer(
        server=server,
        status=status,
        ipv4=ipv4,
        ipv6=ipv6,
        cnames=cnames,
        elapsed_ms=first.elapsed_ms,
    )


def _ask_windows(domain: str) -> ResolverAnswer:
    """Как имя видят обычные программы: с учётом файла hosts и шифрованного DNS Windows."""
    from utils.net_resolve import resolve_ips

    server = DnsServer(label="Windows", address="", kind=SERVER_WINDOWS)
    started = time.perf_counter()
    ipv4, ipv6 = resolve_ips(domain, timeout=DNS_TIMEOUT_S * 2)
    elapsed = (time.perf_counter() - started) * 1000.0
    if not ipv4 and not ipv6:
        return ResolverAnswer(server=server, status=STATUS_ERROR, level=LEVEL_UNKNOWN, elapsed_ms=elapsed)
    return ResolverAnswer(server=server, status=STATUS_OK, ipv4=tuple(ipv4), ipv6=tuple(ipv6), elapsed_ms=elapsed)


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
    pointer = reverse_name(ip)
    if pointer.endswith(".in-addr.arpa"):
        name = pointer[: -len(".in-addr.arpa")] + ".origin.asn.cymru.com"
    else:
        name = pointer[: -len(".ip6.arpa")] + ".origin6.asn.cymru.com"
    result = _ask_helpers(name, TYPE_TXT, servers)
    texts = result.values(TYPE_TXT) if result is not None else ()
    if not texts:
        return None
    parts = [part.strip() for part in texts[0].split("|")]
    asn = parts[0].split()[0] if parts and parts[0] else ""
    info = NetworkInfo(
        asn=asn,
        prefix=parts[1] if len(parts) > 1 else "",
        country=parts[2] if len(parts) > 2 else "",
    )
    if not asn:
        return info
    owner = _ask_helpers(f"AS{asn}.asn.cymru.com", TYPE_TXT, servers)
    owner_texts = owner.values(TYPE_TXT) if owner is not None else ()
    if owner_texts:
        info = replace(info, owner=owner_texts[0].split("|")[-1].strip())
    return info


def _lookup_cert(ip: str, key: str, server_name: str | None) -> NeighborSource:
    result = fetch_cert_names(ip, server_name=server_name)
    if not result.ok:
        if result.kind == "tls" and server_name is None:
            # Сети доставки (Cloudflare и подобные) без имени сайта сертификат не отдают.
            return NeighborSource(key=key, status=SOURCE_SKIPPED, detail="сервер не отдаёт сертификат без имени сайта")
        if server_name is not None and result.kind in ("timeout", "tls", "error"):
            # Порт открыт, а защищённое соединение с именем сайта не складывается.
            return NeighborSource(
                key=key,
                status=SOURCE_ERROR,
                detail=f"соединение с именем {server_name} не установилось — так бывает, когда сайт блокируют по имени",
            )
        return NeighborSource(key=key, status=SOURCE_ERROR, detail=result.detail)
    names = list(result.names)
    if result.common_name and result.common_name.lower() not in names:
        names.insert(0, result.common_name.lower())
    return NeighborSource(key=key, status=SOURCE_OK if names else SOURCE_EMPTY, names=tuple(names))


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
    return NeighborSource(key=key, status=SOURCE_ERROR, detail=f"сервис недоступен ({type(exc).__name__})")


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
    someone_resolved = any(item.status == STATUS_OK and item.addresses for item in items)
    result: list[ResolverAnswer] = []
    for item in items:
        level, note = LEVEL_OK, ""
        if item.status == STATUS_OK:
            order = {LEVEL_OK: 0, LEVEL_UNKNOWN: 1, LEVEL_WARN: 2, LEVEL_FAIL: 3}
            notes: list[str] = []
            for ip in item.addresses:
                ip_level, ip_note = classify_ip(ip)
                if order[ip_level] > order[level]:
                    level = ip_level
                if ip_note and ip_note not in notes:
                    notes.append(ip_note)
            note = ", ".join(notes)
        elif item.status in (STATUS_NXDOMAIN, STATUS_EMPTY):
            if someone_resolved:
                level = LEVEL_WARN
                note = "у других серверов адрес есть"
            else:
                level = LEVEL_UNKNOWN
        elif item.status == STATUS_REFUSED:
            level, note = LEVEL_UNKNOWN, item.note
        else:
            level, note = LEVEL_UNKNOWN, item.note
        result.append(replace(item, level=level, note=note))
    return tuple(result)


def pick_primary_ip(answers: Iterable[ResolverAnswer]) -> str:
    """Адрес для пинга и поиска соседей.

    Сначала тот, которым реально пользуются программы на компьютере (строка
    Windows), иначе самый частый среди серверов. Заглушки берём в последнюю
    очередь: если других адресов нет, полезно увидеть, что отвечает именно она.
    """
    items = list(answers)

    def clean(addresses: Iterable[str]) -> list[str]:
        return [ip for ip in addresses if classify_ip(ip)[0] == LEVEL_OK]

    for item in items:
        if item.server.kind == SERVER_WINDOWS:
            good = clean(item.ipv4)
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

    def stopped(self) -> bool:
        try:
            return bool(self._should_stop and self._should_stop())
        except Exception:
            return False

    def expired(self) -> bool:
        return time.monotonic() >= self._deadline

    def halted(self) -> bool:
        return self.stopped() or self.expired()

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
        self.pool.shutdown(wait=False, cancel_futures=True)


def run_domain_lookup(
    target: str,
    *,
    servers: Iterable[DnsServer] = (),
    use_external: bool = True,
    on_stage: Callable[[DomainLookupReport], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> DomainLookupReport:
    """Полная проверка домена или адреса.

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

    try:
        if kind == KIND_DOMAIN:
            report = _resolve_stage(run, report, server_list, publish)
            primary_ip = pick_primary_ip(report.answers)
        else:
            primary_ip = value
        report = publish(replace(report, primary_ip=primary_ip))

        if primary_ip and not run.halted():
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


def _resolve_stage(run: _Run, report: DomainLookupReport, servers, publish) -> DomainLookupReport:
    domain = report.target
    futures: dict[Future, object] = {run.pool.submit(_ask_windows, domain): -1}
    for index, server in enumerate(servers):
        futures[run.pool.submit(_ask_server, server, domain)] = index
    futures[run.pool.submit(canary_answered, domain)] = "canary"

    collected: dict[int, ResolverAnswer] = {}
    intercepted = False
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


def _address_stage(run: _Run, report: DomainLookupReport, servers, use_external: bool, publish) -> DomainLookupReport:
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
        run.pool.submit(_tcp_connect, ip): "tcp",
        run.pool.submit(_lookup_ptr, ip, helpers): SOURCE_PTR,
    }
    ipv6 = pick_ipv6(report.answers, ip)
    if ipv6:
        futures[run.pool.submit(_ping, ipv6, run.halted)] = "ping6"
    if domain:
        order.append(SOURCE_CERT_NAMED)
        futures[run.pool.submit(_lookup_cert, ip, SOURCE_CERT_NAMED, domain)] = SOURCE_CERT_NAMED
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
    "annotate_answers",
    "classify_ip",
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
]
