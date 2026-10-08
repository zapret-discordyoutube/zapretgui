"""Единственный выход BlockCheck в сеть.

Все обращения прогона наружу идут через функции этого файла: запрос страницы,
вопрос к DNS системы, эталон по DNS-over-HTTPS, файл hosts. Движок и проверки
разделов сами сеть не трогают — поэтому у каждого запроса один и тот же срок,
одна и та же отмена по «Стоп», и подменить сеть в тестах можно в одном месте.

Здесь только факты («что ответили»). Выводы из них делают другие модули.
"""

from __future__ import annotations

import functools
import gzip
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import Future

from diagnostics.limits import DNS_TIMEOUT, DOH_TIMEOUT, HTTPS_TIMEOUT, READ_TIMEOUT, REFERENCE_GRACE_S
from diagnostics.run_context import Run
from diagnostics.tls_probe import ProbeResult, https_get
from utils.dns_reference import REFERENCE_RESOLVERS, ReferenceResolver
from utils.dns_wire import TYPE_A, DnsQueryResult, query_doh
from utils.windows_dns_query import (
    DnsAnswer,
    hosts_file_ipv4,
    query_ipv4,
    system_dns_servers,
)
from utils.windows_icmp import HOP_SILENT, HOP_UNSUPPORTED, trace_hop_ipv4

# Скачивание служебных списков (реестр): общий срок и предел размера.
FILE_TIMEOUT_S = 40.0
FILE_LIMIT_BYTES = 64 * 1024 * 1024

__all__ = [
    "doh_ask",
    "download_file",
    "doh_lookup",
    "fetch",
    "get",
    "hosts_ipv4",
    "known_address",
    "ping_hop",
    "system_ipv4",
    "system_servers",
]


def doh_lookup(run: Run, host: str, record_type: int = TYPE_A) -> tuple[bool, tuple[str, ...]]:
    """Эталонные адреса по DNS-over-HTTPS. (ответил ли хоть один, адреса).

    Спрашиваются все эталонные серверы сразу, ответы складываются. Кто из них
    не ответил и почему — запоминается в прогоне и попадает в отчёт. Молчащего
    сервера сайт не ждёт: после первого ответа остальным даётся ``REFERENCE_GRACE_S``.
    """

    def _one(resolver: ReferenceResolver) -> DnsQueryResult:
        return query_doh(resolver.address, host, record_type, timeout_s=DOH_TIMEOUT, cancel=run.probe_cancel)

    lock = threading.Lock()
    answered = [False]
    ips: list[str] = []
    first_answer = threading.Event()
    left = [len(REFERENCE_RESOLVERS)]
    everyone = threading.Event()

    def _done(resolver: ReferenceResolver, future: Future) -> None:
        # Ответ опоздавшего сервера тоже учитывается в отчёте о серверах, хотя сайт его уже не ждёт.
        try:
            result = None if future.cancelled() else future.result()
        except Exception:
            result = None
        with lock:
            if result is not None:
                run.note_reference(resolver, result)
                answered[0] = answered[0] or result.answered
                for ip in result.values(record_type):
                    if ip not in ips:
                        ips.append(ip)
                if result.answered:
                    first_answer.set()
            left[0] -= 1
            if not left[0]:
                everyone.set()

    for resolver in REFERENCE_RESOLVERS:
        future = run.reference_lane.submit(_one, resolver)
        future.add_done_callback(functools.partial(_done, resolver))
    # Как только ответил первый сервер, остальным даётся ещё секунда: закрытый провайдером
    # эталонный сервер иначе отнимал бы свои пять секунд у каждого проверяемого сайта.
    while not everyone.wait(0.05):
        if first_answer.is_set():
            everyone.wait(REFERENCE_GRACE_S)
            break
    with lock:
        return answered[0], tuple(ips)


def doh_ask(run: Run, name: str, record_type: int) -> DnsQueryResult | None:
    """Произвольный вопрос к эталону: первый ответивший сервер. None — не ответил никто."""
    for resolver in REFERENCE_RESOLVERS:
        result = query_doh(resolver.address, name, record_type, cancel=run.probe_cancel)
        if result.answered:
            return result
    return None


def fetch(
    run: Run,
    host: str,
    ip: str,
    path: str,
    *,
    timeout: float = HTTPS_TIMEOUT,
    read_limit: int = 0,
    read_timeout: float = READ_TIMEOUT,
    body_done: Callable[[bytes], bool] | None = None,
) -> ProbeResult:
    """HTTPS-запрос к сайту ``host`` по адресу ``ip``. Снимается кнопкой «Стоп»."""
    return https_get(
        host,
        ip,
        path,
        timeout=timeout,
        read_limit=read_limit,
        read_timeout=read_timeout,
        body_done=body_done,
        cancel=run.probe_cancel,
    )


def get(run: Run, host: str, ip: str, path: str, *, read_limit: int = 0) -> ProbeResult:
    """Обычный запрос «открывается ли сайт» с общими сроками."""
    return fetch(run, host, ip, path, read_limit=read_limit)


def download_file(url: str, etag: str = "", *, timeout: float = FILE_TIMEOUT_S, limit: int = FILE_LIMIT_BYTES):
    """Скачивает файл целиком. (содержимое, метка версии) или None — не вышло.

    ``etag`` — метка версии, которая уже есть: если на сервере та же, файл не
    качается и содержимое возвращается как ``None`` при той же метке.
    Сжатие включено: список реестра по сети идёт вчетверо меньше.
    """
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "ZapretGUI"})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                return None
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            return body, str(response.headers.get("ETag") or "")
    except urllib.error.HTTPError as error:
        return (None, etag) if error.code == 304 else None
    except (OSError, ValueError, EOFError):
        return None


def ping_hop(ip: str, ttl: int, *, timeout_ms: int = 1000) -> tuple[str, float | None] | None:
    """Один пинг со сроком жизни ``ttl``: (адрес ответившего узла, время) или None — ответа нет.

    ``NotImplementedError`` — пинг в этой системе недоступен (не Windows).
    """
    result = trace_hop_ipv4(ip, ttl, timeout_ms=timeout_ms)
    if result.kind == HOP_UNSUPPORTED:
        raise NotImplementedError
    if result.kind == HOP_SILENT:
        return None
    return str(result.address or ""), result.rtt_ms


def system_ipv4(run: Run, host: str) -> DnsAnswer:
    """Ответ DNS системы (без кэша и hosts)."""
    return query_ipv4(host, timeout=DNS_TIMEOUT, cancelled=run.dns_cancelled)


def hosts_ipv4(host: str) -> tuple[str, ...]:
    """Адреса сайта из файла hosts."""
    return tuple(hosts_file_ipv4(host))


def system_servers() -> tuple[str, ...]:
    """DNS-серверы, заданные в системе."""
    return tuple(system_dns_servers())


def known_address(run: Run, host: str) -> str:
    """Адрес сервера: из hosts/DNS системы, иначе эталон. Пусто — не нашли.

    Адрес запоминается на прогон: отправка проверяется на том же адресе, что и загрузка.
    """
    known = run.freeze_addresses.get(host)
    if known is not None:
        return known
    ips = list(hosts_ipv4(host)) or list(system_ipv4(run, host).ips)
    if not ips:
        ips = list(doh_lookup(run, host)[1])
    address = ips[0] if ips else ""
    if address:
        run.freeze_addresses[host] = address
    return address
