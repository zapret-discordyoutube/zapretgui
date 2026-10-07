"""Единственный выход BlockCheck в сеть.

Все обращения прогона наружу идут через функции этого файла: запрос страницы,
вопрос к DNS системы, эталон по DNS-over-HTTPS, файл hosts. Движок и проверки
разделов сами сеть не трогают — поэтому у каждого запроса один и тот же срок,
одна и та же отмена по «Стоп», и подменить сеть в тестах можно в одном месте.

Здесь только факты («что ответили»). Выводы из них делают другие модули.
"""

from __future__ import annotations

from collections.abc import Callable

from diagnostics.limits import DNS_TIMEOUT, DOH_TIMEOUT, HTTPS_TIMEOUT, READ_TIMEOUT
from diagnostics.run_context import Run
from diagnostics.tls_probe import ProbeResult, https_get
from utils.dns_reference import REFERENCE_RESOLVERS, ReferenceResolver
from utils.dns_wire import TYPE_A, DnsQueryResult, query_doh
from utils.windows_dns_query import DnsAnswer, hosts_file_ipv4, query_ipv4, system_dns_servers

__all__ = [
    "doh_ask",
    "doh_lookup",
    "fetch",
    "get",
    "hosts_ipv4",
    "known_address",
    "system_ipv4",
    "system_servers",
]


def doh_lookup(run: Run, host: str, record_type: int = TYPE_A) -> tuple[bool, tuple[str, ...]]:
    """Эталонные адреса по DNS-over-HTTPS. (ответил ли хоть один, адреса).

    Спрашиваются все эталонные серверы сразу, ответы складываются. Кто из них
    не ответил и почему — запоминается в прогоне и попадает в отчёт.
    """

    def _one(resolver: ReferenceResolver) -> DnsQueryResult:
        return query_doh(resolver.address, host, record_type, timeout_s=DOH_TIMEOUT, cancel=run.probe_cancel)

    futures = [(resolver, run.submit(_one, resolver)) for resolver in REFERENCE_RESOLVERS]
    answered = False
    ips: list[str] = []
    for resolver, future in futures:
        result = future.result()
        run.note_reference(resolver, result)
        answered = answered or result.answered
        for ip in result.values(record_type):
            if ip not in ips:
                ips.append(ip)
    return answered, tuple(ips)


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
