"""Работает ли IPv6 в этой сети сам по себе.

Браузер пробует IPv6 первым. Если IPv6 в системе настроен, но не работает
(адрес выдан, а пакеты не ходят), каждый сайт открывается с задержкой: браузер
ждёт и только потом переходит на IPv4. Со стороны это выглядит как «всё
тормозит», и к блокировкам отношения не имеет.

Проверка: к двум заведомо доступным сайтам идёт обычный HTTPS-запрос по их
IPv6-адресам. Адреса берутся у эталонных DNS-серверов, чтобы результат не
зависел от DNS системы.

* Хоть один ответил — IPv6 работает.
* Не ответил ни один, а Windows считает, что дорога по IPv6 есть, — IPv6
  настроен, но сломан.
* Дороги нет — IPv6 в этой сети просто нет, это норма.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from diagnostics.tls_probe import KIND_CANCELLED, ProbeResult

__all__ = [
    "IPV6_ABSENT",
    "IPV6_BROKEN",
    "IPV6_OK",
    "IPV6_UNKNOWN",
    "PROBE_HOSTS",
    "Ipv6Facts",
    "Ipv6Verdict",
    "collect",
    "judge",
]

# У обоих есть IPv6, и их почти никогда не блокируют.
PROBE_HOSTS = ("www.google.com", "www.cloudflare.com")

IPV6_OK = "ok"
IPV6_ABSENT = "absent"
IPV6_BROKEN = "broken"
IPV6_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Ipv6Facts:
    # Считает ли система, что по IPv6 есть дорога в интернет. None — узнать не удалось.
    has_route: bool | None = None
    # (сайт, его IPv6-адрес или "", результат запроса или None, если адреса нет).
    probes: tuple[tuple[str, str, ProbeResult | None], ...] = ()


@dataclass(frozen=True, slots=True)
class Ipv6Verdict:
    code: str
    text: str


def collect(
    *,
    has_route: Callable[[], bool | None],
    lookup: Callable[[str], tuple[str, ...]],
    get: Callable[[str, str], ProbeResult],
    submit: Callable,
    hosts: tuple[str, ...] = PROBE_HOSTS,
) -> Ipv6Facts:
    """``lookup`` — IPv6-адреса сайта у эталонного DNS, ``get`` — HTTPS-запрос по адресу."""

    def one(host: str) -> tuple[str, str, ProbeResult | None]:
        addresses = lookup(host)
        if not addresses:
            return host, "", None
        return host, addresses[0], get(host, addresses[0])

    futures = [submit(one, host) for host in hosts]
    try:
        route = has_route()
    except Exception:
        route = None
    return Ipv6Facts(has_route=route, probes=tuple(future.result() for future in futures))


def judge(facts: Ipv6Facts) -> Ipv6Verdict:
    results = [result for _host, _ip, result in facts.probes if result is not None]
    working = [result for result in results if result.ok]
    if working:
        fastest = min(result.elapsed_ms for result in working)
        return Ipv6Verdict(IPV6_OK, f"работает (ответ за {max(1, round(fastest))} мс)")
    if any(result.kind == KIND_CANCELLED for result in results):
        return Ipv6Verdict(IPV6_UNKNOWN, "проверить не успели")
    if facts.has_route is False:
        return Ipv6Verdict(IPV6_ABSENT, "в этой сети его нет — сайты открываются по IPv4, это нормально")
    if not results:
        # Эталонные серверы не дали адресов: судить о самом IPv6 не по чему.
        return Ipv6Verdict(IPV6_UNKNOWN, "проверить не удалось: не получили адреса проверочных сайтов")
    if facts.has_route is None:
        return Ipv6Verdict(IPV6_UNKNOWN, "проверочные сайты по IPv6 не ответили, а есть ли он в системе — узнать не удалось")
    return Ipv6Verdict(
        IPV6_BROKEN,
        "настроен, но не работает: Windows считает, что дорога по IPv6 есть, а проверочные сайты по нему не отвечают",
    )
