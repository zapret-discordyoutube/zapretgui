"""Журнал сети браузера (``--log-net-log``): что браузер на самом деле делал с сайтом.

Когда браузер без окна сайт не открыл, догадки («мог получить другой адрес»,
«мог пойти через прокси») не нужны: всё это есть в журнале сети, который браузер
пишет по ключу ``--log-net-log``. Отсюда берутся факты:

* к какому адресу браузер подключался (IPv4 или IPv6) и чем кончилось каждое
  соединение — ошибкой, молчанием или ответом;
* прятал ли он имя сайта (ECH): ключ ECH браузер берёт из записи HTTPS в DNS;
* спрашивал ли он адрес у своего шифрованного DNS или у DNS системы;
* шёл ли напрямую или через прокси;
* какие ещё имена ждала страница и какие из них не открылись.

По этим фактам проверка после «Применить» решает, что изменить в следующем
открытии (``blockcheck.browser_diagnosis``). Здесь нет сети: только чтение файла.

Журнал пишется построчно: первая строка — словари констант, затем по событию на
строку. Браузер, которого погасили, не дописывает хвост; читаются целые строки.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

__all__ = ["HUNG", "Connection", "NetFacts", "parse_netlog", "read_netlog"]

# Соединение не кончилось ни ответом, ни ошибкой: браузер ждал, пока его не погасили.
HUNG = "hung"

_GROUP_HOST = re.compile(r"^(?:https?|wss?)://\[?([^\]\s:/>]+)")
_URL_HOST = re.compile(r"^(?:https?|wss?)://\[?([^\]\s:/?#>]+)")


@dataclass(frozen=True, slots=True)
class Connection:
    host: str
    address: str
    # Браузер спрятал имя сайта (ECH): в приветствии был ключ ECH.
    ech: bool = False
    # ERR_… — соединение оборвалось; HUNG — не кончилось; пусто — шифрование договорено.
    error: str = ""
    received: int = 0

    @property
    def ipv6(self) -> bool:
        return ":" in self.address

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(frozen=True, slots=True)
class NetFacts:
    connections: tuple[Connection, ...] = ()
    # Имя → прокси, через который браузер к нему шёл (только не «напрямую»).
    proxies: dict[str, str] = field(default_factory=dict)
    # Имена, адрес которых браузер спрашивал у своего шифрованного DNS.
    secure_dns: frozenset[str] = frozenset()
    # Имя → ошибка DNS, когда адрес не нашёлся.
    dns_errors: dict[str, str] = field(default_factory=dict)
    # Запросы страницы: (имя, ошибка); HUNG — ответа не дождались. Только запросы самой страницы, без служебных.
    failed_requests: tuple[tuple[str, str], ...] = ()

    def for_host(self, host: str) -> tuple[Connection, ...]:
        return tuple(connection for connection in self.connections if connection.host == host)

    def failed_other_hosts(self, host: str) -> tuple[str, ...]:
        """Другие имена, которые страница ждала, а они не открылись, — по порядку, без повторов."""
        return tuple(dict.fromkeys(name for name, _error in self.failed_requests if name != host))


def _parse_lines(text: str) -> tuple[dict, list[dict]]:
    lines = text.splitlines()
    if not lines:
        return {}, []
    head = lines[0].rstrip().rstrip(",")
    try:
        constants = json.loads(head + ("}" if not head.endswith("}}") else ""))["constants"]
    except (ValueError, KeyError):
        try:
            whole = json.loads(text)
            return whole.get("constants", {}), list(whole.get("events", []))
        except ValueError:
            return {}, []
    events = []
    for line in lines[2:]:
        line = line.strip().rstrip(",")
        # Последнее событие закрывает список: «…}]».
        line = line.removesuffix("]")
        if not line.startswith("{"):
            break
        try:
            events.append(json.loads(line))
        except ValueError:
            break
    return constants, events


def parse_netlog(text: str, site: str = "") -> NetFacts:
    """Факты из текста журнала. ``site`` — имя проверяемого сайта: по нему отбираются запросы страницы."""
    constants, events = _parse_lines(text)
    if not constants or not events:
        return NetFacts()
    types = {value: name for name, value in constants.get("logEventTypes", {}).items()}
    sources = {value: name for name, value in constants.get("logSourceType", {}).items()}
    errors = {value: name for name, value in constants.get("netError", {}).items()}

    def error_name(code) -> str:
        name = errors.get(code, str(code))
        return name if name.startswith("ERR_") else f"ERR_{name}"

    by_source: dict[int, list[dict]] = defaultdict(list)
    for event in events:
        by_source[event.get("source", {}).get("id")].append(event)

    socket_host: dict[int, str] = {}
    proxies: dict[str, str] = {}
    secure_dns: set[str] = set()
    dns_errors: dict[str, str] = {}
    requests: list[tuple[str, str, str, str]] = []  # (имя, ошибка, ключ изоляции, вид запроса)
    for items in by_source.values():
        kind = sources.get(items[0].get("source", {}).get("type"))
        if kind in ("SSL_CONNECT_JOB", "TRANSPORT_CONNECT_JOB", "SOCKS_CONNECT_JOB", "HTTP_PROXY_CONNECT_JOB"):
            host = ""
            for event in items:
                params = event.get("params", {})
                match = _GROUP_HOST.match(str(params.get("group_id", "")))
                if match:
                    host = match.group(1).lower()
                dependency = params.get("source_dependency", {})
                if host and dependency and types.get(event.get("type")) in ("CONNECT_JOB_SET_SOCKET", "TCP_CONNECT_JOB_CONNECTOR_CONNECT_START"):
                    socket_host.setdefault(dependency.get("id"), host)
        elif kind == "HTTP_STREAM_JOB_CONTROLLER":
            host = ""
            for event in items:
                params = event.get("params", {})
                match = _URL_HOST.match(str(params.get("url", "")))
                if match:
                    host = match.group(1).lower()
                proxy = str(params.get("proxy_info", ""))
                if host and types.get(event.get("type")) == "PROXY_RESOLUTION_SERVICE_RESOLVED_PROXY_LIST" and proxy and proxy != "DIRECT":
                    proxies.setdefault(host, proxy.removeprefix("PROXY ").split(";")[0].strip())
        elif kind == "HOST_RESOLVER_IMPL_JOB":
            host = ""
            for event in items:
                params = event.get("params", {})
                name = types.get(event.get("type"))
                if name == "HOST_RESOLVER_MANAGER_JOB":
                    match = _URL_HOST.match(str(params.get("host", "")))
                    host = match.group(1).lower() if match else host
                    if host and event.get("phase") == 2 and params.get("net_error"):
                        dns_errors[host] = error_name(params["net_error"])
                if host and name == "HOST_RESOLVER_DNS_TASK" and params.get("secure") is True:
                    secure_dns.add(host)
        elif kind == "URL_REQUEST":
            url = error = isolation = request_type = ""
            finished = cancelled = False
            for event in items:
                params = event.get("params", {})
                name = types.get(event.get("type"))
                if name == "REQUEST_ALIVE" and event.get("phase") == 1:
                    url = str(params.get("url", ""))
                if name == "URL_REQUEST_START_JOB" and params.get("network_isolation_key"):
                    isolation = str(params["network_isolation_key"]).split(" ")[0]
                    request_type = str(params.get("request_type", ""))
                # Снят без ошибки — браузер перестал ждать ответа (его срок вышел): это то же, что не дождались.
                if name == "CANCELLED":
                    cancelled = True
                if name == "REQUEST_ALIVE" and event.get("phase") == 2:
                    finished = True
                    code = params.get("net_error")
                    error = error_name(code) if code else (HUNG if cancelled else "")
            match = _URL_HOST.match(url)
            if match:
                requests.append((match.group(1).lower(), error if finished else HUNG, isolation, request_type))

    connections = []
    for source_id, items in by_source.items():
        if sources.get(items[0].get("source", {}).get("type")) != "SOCKET" or source_id not in socket_host:
            continue
        address = ""
        ech = False
        error = HUNG
        received = 0
        for event in items:
            params = event.get("params", {})
            name = types.get(event.get("type"))
            if name == "TCP_CONNECT_ATTEMPT" and params.get("address"):
                address = str(params["address"])
            elif name == "SSL_ENCRYPTED_CLIENT_HELLO":
                ech = True
            elif name == "SOCKET_BYTES_RECEIVED":
                received += int(params.get("byte_count", 0) or 0)
            elif name == "SSL_CONNECT" and event.get("phase") == 2:
                error = error_name(params["net_error"]) if params.get("net_error") else ""
        if address:
            connections.append(Connection(socket_host[source_id], _strip_port(address), ech, error, received))

    # Запросы самой страницы: тот же ключ изоляции, что у запроса к сайту, и не служебные («other»: шифрованный DNS,
    # отчёты об ошибках вроде a.nel.cloudflare.com, запросы самого браузера) — их страница не ждёт.
    page_keys = {isolation for host, _error, isolation, _kind in requests if site and host == site.lower() and isolation}
    failed = tuple(
        (host, error)
        for host, error, isolation, kind in requests
        if error and error != "ERR_ABORTED" and isolation in page_keys and (kind != "other" or host == site.lower())
    )
    return NetFacts(tuple(connections), proxies, frozenset(secure_dns), dns_errors, failed if page_keys else ())


def _strip_port(address: str) -> str:
    if address.startswith("["):
        return address[1:].split("]")[0]
    return address.rsplit(":", 1)[0] if address.count(":") == 1 else address


def read_netlog(path: str, site: str = "") -> NetFacts:
    """Факты из файла журнала; файла нет или он пуст — фактов нет."""
    try:
        with open(path, encoding="utf-8", errors="replace") as source:
            return parse_netlog(source.read(), site)
    except OSError:
        return NetFacts()
