"""Нынешний адрес своего сервера Zapret DNS: встроенный или узнанный по имени.

Сервер переезжает (``config.zapret_dns``). Встроенный в программу адрес верен на
день выпуска; после переезда люди остались бы на старом сервере до новой версии.
Поэтому программа сама спрашивает адрес имени сервера (раз в сутки, через шлюз
проб) и проверяет каждый найденный адрес настоящим запросом DoH с проверкой
сертификата этого имени (``dns.doh_lookup.find_doh_addresses``): подменённый
провайдером или чужой адрес сертификат сервера не предъявит.

Проверенный адрес хранится в настройках (``dns.zapret_dns_server``) и с этого
момента — нынешний для всей программы:

- плитка «Zapret DNS» (``dns.dns_providers``) получает его в свою запись
  (``adopt``), и замена старых адресов на адаптерах переводит людей на него;
- профиль hosts «Zapret DNS» подставляет его на место метки каталога
  (``hosts.catalog_repository``), и блок hosts переписывается сам.

Отметка (``check_in``). Посредник сервера будет пускать только тех, кто
спрашивает его DNS (сервер помнит такие адреса 6 часов). Человек с профилем hosts
«Zapret DNS» сам этот DNS не спрашивает — имена у него в файле hosts. Поэтому,
пока профиль включён, программа раз в два часа (и сразу после включения) задаёт
серверу один обычный вопрос DNS: имя ``hello.check.<имя сервера>``. Ничего о
человеке в нём нет.

Имя не ответило или ни один адрес не прошёл проверку — остаётся прежний адрес:
неудачный вопрос никогда не меняет сервер.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass

from config import zapret_dns
from log.log import log

__all__ = [
    "CHECK_IN_NAME",
    "ServerChange",
    "active_ipv4",
    "check_in",
    "hosts_profile_in_use",
    "address_replacements",
    "adopt",
    "dropped_ipv6",
    "current_ipv4",
    "former_ipv4",
    "refresh",
]

# Сколько прежних узнанных адресов помнить, чтобы перевести с них адаптеры.
MAX_LEARNED_HISTORY = 8

# Нынешний адрес в памяти: читается без диска (каталог hosts, плитка). До adopt — встроенный.
_ACTIVE_IPV4: tuple[str, ...] = zapret_dns.IPV4


@dataclass(frozen=True, slots=True)
class ServerChange:
    """Итог вопроса об адресе: changed — нынешний адрес стал другим."""

    ipv4: tuple[str, ...]
    changed: bool
    detail: str = ""


def _global_ipv4(values) -> tuple[str, ...]:
    result: list[str] = []
    for value in values or ():
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError:
            continue
        text = address.compressed
        if address.version == 4 and address.is_global and text not in result:
            result.append(text)
    return tuple(result)


def _stored() -> dict:
    from settings import store

    return store.get_zapret_dns_server()


def active_ipv4() -> tuple[str, ...]:
    """Нынешний адрес сервера без обращения к диску: встроенный или поставленный adopt."""
    return _ACTIVE_IPV4


def current_ipv4(stored: dict | None = None) -> tuple[str, ...]:
    """Адреса IPv4 сервера сейчас: проверенные по имени, а без них — встроенные."""
    learned = _global_ipv4((stored if stored is not None else _stored()).get("ipv4"))
    return learned or zapret_dns.IPV4


def former_ipv4(stored: dict | None = None) -> tuple[str, ...]:
    """Все адреса, на которых сервер был раньше, кроме нынешних: с них переводят адаптеры."""
    stored = stored if stored is not None else _stored()
    current = set(current_ipv4(stored))
    known = (*zapret_dns.FORMER_IPV4, *zapret_dns.IPV4, *_global_ipv4(stored.get("former_ipv4")))
    return tuple(address for address in dict.fromkeys(known) if address not in current)


def address_replacements(stored: dict | None = None) -> dict[str, str]:
    """Пары «прежний адрес сервера → нынешний» для адаптеров; прежний IPv6 → нынешний IPv6, если он есть."""
    stored = stored if stored is not None else _stored()
    current = current_ipv4(stored)
    pairs = {address: current[0] for address in former_ipv4(stored)}
    if zapret_dns.IPV6:
        pairs.update({address: zapret_dns.IPV6[0] for address in zapret_dns.FORMER_IPV6 if address not in zapret_dns.IPV6})
    return pairs


def dropped_ipv6() -> tuple[str, ...]:
    """Прежние IPv6 сервера, которые убираются с адаптеров: у нынешнего сервера IPv6 нет."""
    return () if zapret_dns.IPV6 else zapret_dns.FORMER_IPV6


def adopt(stored: dict | None = None) -> tuple[str, ...]:
    """Ставит нынешний адрес в запись плитки «Zapret DNS» и сбрасывает кэш каталога hosts.

    Запись плитки — один словарь каталога провайдеров; список адресов меняется на
    месте, поэтому все, кто читает каталог, видят новый адрес. Вызывается до
    замены старых адресов на адаптерах — и при запуске, и после переезда.
    """
    from dns.dns_providers import zapret_dns_provider
    from hosts.proxy_domains import invalidate_hosts_catalog_cache

    global _ACTIVE_IPV4
    addresses = current_ipv4(stored)
    entry = zapret_dns_provider()
    entry["ipv4"][:] = list(addresses)
    if addresses != _ACTIVE_IPV4:
        _ACTIVE_IPV4 = addresses
        invalidate_hosts_catalog_cache()
    return addresses


CHECK_IN_NAME = f"hello.check.{zapret_dns.HOST}"
CHECK_IN_TIMEOUT_S = 3.0


def hosts_profile_in_use() -> bool:
    """Включён ли у человека профиль hosts «Zapret DNS» хотя бы у одного сервиса."""
    from hosts.proxy_domains import load_user_hosts_selection

    return zapret_dns.CATALOG_PROFILE_ID in set(load_user_hosts_selection().values())


def check_in(*, cancel=None, query=None) -> bool:
    """Один вопрос DNS серверу по UDP, чтобы его посредник узнал этот адрес. Ответ — любой.

    Ходит в сеть: вызывать по кнопке или с пропуском шлюза проб.
    """
    from utils.dns_wire import TYPE_A, query_udp

    query = query_udp if query is None else query
    address = active_ipv4()[0]
    result = query(address, CHECK_IN_NAME, TYPE_A, timeout_s=CHECK_IN_TIMEOUT_S, cancel=cancel)
    answered = bool(getattr(result, "answered", False))
    log(f"Zapret DNS: отметка для посредника {address}: {'ответил' if answered else 'не ответил'}", "DEBUG")
    return answered


def refresh(*, cancel=None, lookup=None, now: float | None = None) -> ServerChange:
    """Спрашивает адрес имени сервера и, если он проверен и другой, делает его нынешним.

    Ходит в сеть: вызывать только с пропуском шлюза проб (``utils.probe_gate``).
    """
    from dns.custom_servers import parse_doh_template
    from settings import store

    if lookup is None:
        from dns.doh_lookup import find_doh_addresses

        lookup = find_doh_addresses
    template = parse_doh_template(zapret_dns.DOH_URL)
    found = lookup(template, ipv6=False, doh_supported=True, cancel=cancel)
    learned = _global_ipv4(getattr(found, "ipv4", ()))
    before = _stored()
    current = current_ipv4(before)
    if not learned:
        reason = str(getattr(found, "error", "") or "нет проверенного адреса")
        return ServerChange(current, False, reason)
    if learned == current:
        store.set_zapret_dns_server(
            ipv4=list(learned),
            former_ipv4=list(_global_ipv4(before.get("former_ipv4"))),
            checked_at=time.time() if now is None else now,
        )
        return ServerChange(learned, False, "адрес не изменился")
    history = [address for address in (*current, *_global_ipv4(before.get("former_ipv4"))) if address not in learned]
    store.set_zapret_dns_server(
        ipv4=list(learned),
        former_ipv4=list(dict.fromkeys(history))[:MAX_LEARNED_HISTORY],
        checked_at=time.time() if now is None else now,
    )
    log(f"Zapret DNS: сервер переехал: {', '.join(current)} -> {', '.join(learned)}", "INFO")
    adopt()
    return ServerChange(learned, True, "сервер переехал")
