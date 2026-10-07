"""Поиск IP-адресов сервера по его адресу DoH.

Windows не умеет «просто строку» https://имя/dns-query: в настройках
адаптера всегда стоят IP-адреса, а шифрование включается для пары
«адрес + шаблон DoH». Поэтому, когда пользователь задал только адрес DoH,
программа сама узнаёт IP сервера и проверяет каждый.

Шаги:
1. кандидаты — адреса имени сервера: шифрованный вопрос эталонным серверам
   (utils.dns_reference) и DNS системы; берётся первый ответ и те, что
   пришли сразу за ним;
2. проверка — каждому кандидату отправляется настоящий запрос DoH с именем
   сервера и проверкой сертификата. Адрес, подменённый провайдером,
   сертификат не предъявит и проверку не пройдёт;
3. если Windows сама DoH не умеет (Windows 10), адрес должен отвечать ещё и
   на обычный DNS — иначе с ним не откроется ни один сайт.

В запись сервера попадает не больше MAX_PER_FAMILY проверенных адресов
каждой версии: основной и запасной.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from dns.custom_servers import DohTemplate
from utils.dns_reference import REFERENCE_RESOLVERS
from utils.dns_wire import (
    ENCRYPTED_TIMEOUT_S,
    TYPE_A,
    TYPE_AAAA,
    DnsQueryResult,
    failure_text,
    query_doh,
    query_udp,
)
from utils.socket_cancel import SocketCancel

PROBE_DOMAIN = "example.com"
MAX_PER_FAMILY = 2
MAX_CANDIDATES = 8
PLAIN_TIMEOUT_S = 1.5
# Сколько ещё ждать остальных ответов после первого удачного: успевают AAAA и DNS системы.
ANSWER_GRACE_S = 0.4


@dataclass(frozen=True, slots=True)
class DohLookup:
    """Что нашлось: проверенные адреса или причина, почему их нет.

    notice — оговорка к удачному поиску (например, что шифрования на этой
    Windows не будет).
    """

    host: str
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()
    error: str = ""
    notice: str = ""

    @property
    def found(self) -> bool:
        return bool(self.ipv4 or self.ipv6) and not self.error


def _is_ipv6(address: str) -> bool:
    return ":" in address


def resolve_candidates(host: str, *, ipv6: bool, cancel: SocketCancel | None = None) -> list[str]:
    """Адреса имени сервера: сначала от эталонных серверов по DoH, затем от DNS системы."""
    rtypes = (TYPE_A, TYPE_AAAA) if ipv6 else (TYPE_A,)
    questions = [(resolver.address, rtype) for resolver in REFERENCE_RESOLVERS for rtype in rtypes]

    def ask(question: tuple[str, int]) -> tuple[str, ...]:
        address, rtype = question
        return query_doh(address, host, rtype, timeout_s=ENCRYPTED_TIMEOUT_S, cancel=cancel).values(rtype)

    def ask_system() -> tuple[str, ...]:
        try:
            found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            return ()
        return tuple(str(item[4][0]) for item in found)

    # Закрытый на линии эталонный сервер молчит до тайм-аута. Ждать всех незачем:
    # хватит первого, кто назвал адреса, — каждый кандидат всё равно проверяется.
    pool = ThreadPoolExecutor(max_workers=len(questions) + 1)
    try:
        futures = [pool.submit(ask, question) for question in questions]
        futures.append(pool.submit(ask_system))
        pending = set(futures)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            if any(future.exception() is None and future.result() for future in done):
                break
        if pending:
            wait(pending, timeout=ANSWER_GRACE_S)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    result: list[str] = []
    for future in futures:
        if not future.done() or future.cancelled() or future.exception() is not None:
            continue
        for value in future.result():
            if value not in result and (ipv6 or not _is_ipv6(value)):
                result.append(value)
    return result


def probe_doh(address: str, template: DohTemplate, cancel: SocketCancel | None = None) -> DnsQueryResult:
    """Настоящий запрос DoH по адресу: сертификат проверяется для имени сервера."""
    return query_doh(
        address,
        PROBE_DOMAIN,
        TYPE_A,
        tls_host="" if template.host_is_address else template.host,
        port=template.port,
        path=template.path,
        timeout_s=ENCRYPTED_TIMEOUT_S,
        cancel=cancel,
    )


def probe_plain(address: str, cancel: SocketCancel | None = None) -> DnsQueryResult:
    """Обычный DNS-запрос по адресу (UDP, порт 53)."""
    return query_udp(address, PROBE_DOMAIN, TYPE_A, timeout_s=PLAIN_TIMEOUT_S, cancel=cancel)


def _reason(result: DnsQueryResult) -> str:
    return failure_text(result) or result.detail or "нет ответа"


def find_doh_addresses(
    template: DohTemplate,
    *,
    ipv6: bool = False,
    doh_supported: bool = True,
    cancel: SocketCancel | None = None,
    resolve: Callable[..., list[str]] = resolve_candidates,
    probe: Callable[..., DnsQueryResult] = probe_doh,
    plain: Callable[..., DnsQueryResult] = probe_plain,
) -> DohLookup:
    """Проверенные IP-адреса сервера DoH или понятная причина, почему их нет.

    ipv6 — есть ли у компьютера дорога по IPv6: без неё адреса IPv6 не
    проверить, и в запись они не попадают. doh_supported — умеет ли эта
    Windows шифровать DNS сама.
    """
    host = template.host
    if template.host_is_address:
        candidates = [host] if ipv6 or not _is_ipv6(host) else []
    else:
        candidates = resolve(host, ipv6=ipv6, cancel=cancel)
    candidates = candidates[:MAX_CANDIDATES]
    if not candidates:
        return DohLookup(
            host=host,
            error=(
                f"Не удалось узнать IP-адреса сервера {host}: имя не находится. "
                "Проверьте адрес DoH или впишите IP-адреса сервера сами."
            ),
        )

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        answers = list(pool.map(lambda address: probe(address, template, cancel), candidates))
    working = [address for address, answer in zip(candidates, answers) if answer.answered]
    if not working:
        return DohLookup(
            host=host,
            error=(
                f"Сервер {host} найден ({', '.join(candidates[:3])}), но на запрос DoH не ответил: "
                f"{_reason(answers[0])}. Проверьте адрес DoH; возможно, сервер закрыт на вашей линии."
            ),
        )

    notice = ""
    if not doh_supported:
        with ThreadPoolExecutor(max_workers=len(working)) as pool:
            plain_answers = list(pool.map(lambda address: plain(address, cancel), working))
        working = [address for address, answer in zip(working, plain_answers) if answer.answered]
        if not working:
            return DohLookup(
                host=host,
                error=(
                    f"Сервер {host} отвечает только по DoH, а эта Windows сама шифровать DNS не умеет "
                    "(нужна Windows 11). Работать он здесь не будет — выберите плитку из группы «Шифрованные»."
                ),
            )
        notice = (
            "Эта Windows сама шифровать DNS не умеет (нужна Windows 11): запросы к серверу пойдут обычным DNS."
        )

    return DohLookup(
        host=host,
        ipv4=tuple(address for address in working if not _is_ipv6(address))[:MAX_PER_FAMILY],
        ipv6=tuple(address for address in working if _is_ipv6(address))[:MAX_PER_FAMILY],
        notice=notice,
    )


__all__ = [
    "MAX_PER_FAMILY",
    "PROBE_DOMAIN",
    "DohLookup",
    "find_doh_addresses",
    "probe_doh",
    "probe_plain",
    "resolve_candidates",
]
