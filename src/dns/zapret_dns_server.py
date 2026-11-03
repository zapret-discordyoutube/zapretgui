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
- замена старых адресов на адаптерах переводит людей на него.

С 2026-11-03 сервер за Cloudflare и отвечает только по DoH: имя даёт адреса
Cloudflare, они и становятся адресами плитки.

Посредник (``learn_relay``). Имена сервисов из списка сервер отдаёт адресом своего
посредника, а не своим: свой адрес закрыт в России по IP. Этот адрес знает только
сам сервер, в общем DNS его нет (решение пользователя: только для внутреннего
употребления). Вместе с адресом имени программа спрашивает сервер по DoH об имени
``config.zapret_dns.RELAY_NAME`` и хранит ответ рядом (``relay_ipv4``); профиль
hosts «Zapret DNS» подставляет его на место метки каталога
(``hosts.catalog_repository``), и блок hosts переписывается сам. Адреса посредника
нет (не узнан или выключен) — строки профиля не пишутся вовсе.

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
    "ServerChange",
    "active_ipv4",
    "active_relay_ipv4",
    "address_replacements",
    "adopt",
    "dropped_ipv6",
    "current_ipv4",
    "former_ipv4",
    "learn_relay",
    "refresh",
]

# Сколько прежних узнанных адресов помнить, чтобы перевести с них адаптеры.
MAX_LEARNED_HISTORY = 8

# Нынешний адрес в памяти: читается без диска (каталог hosts, плитка). До adopt — встроенный.
_ACTIVE_IPV4: tuple[str, ...] = zapret_dns.IPV4
# Адрес посредника в памяти (каталог hosts). До adopt — пусто: встроенного нет намеренно.
_ACTIVE_RELAY: tuple[str, ...] = ()
RELAY_TIMEOUT_S = 6.0


@dataclass(frozen=True, slots=True)
class ServerChange:
    """Итог вопроса об адресе: changed — нынешний адрес стал другим, relay_changed — адрес посредника."""

    ipv4: tuple[str, ...]
    changed: bool
    detail: str = ""
    relay_changed: bool = False


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


def active_relay_ipv4() -> tuple[str, ...]:
    """Адрес посредника без обращения к диску: поставленный adopt; пусто — не узнан или выключен."""
    return _ACTIVE_RELAY


def active_ipv4() -> tuple[str, ...]:
    """Нынешний адрес сервера без обращения к диску: встроенный или поставленный adopt."""
    return _ACTIVE_IPV4


def current_ipv4(stored: dict | None = None) -> tuple[str, ...]:
    """Адреса IPv4 сервера сейчас: проверенные по имени, а без них — встроенные.

    Узнанный адрес, который выпуск уже числит прежним (``FORMER_IPV4``), устарел: так
    люди с новой версией уходят со старого адреса сразу, не дожидаясь вопроса раз в сутки.
    """
    learned = _global_ipv4((stored if stored is not None else _stored()).get("ipv4"))
    learned = tuple(address for address in learned if address not in zapret_dns.FORMER_IPV4)
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

    global _ACTIVE_IPV4, _ACTIVE_RELAY
    stored = stored if stored is not None else _stored()
    addresses = current_ipv4(stored)
    relay = _global_ipv4(stored.get("relay_ipv4"))
    entry = zapret_dns_provider()
    entry["ipv4"][:] = list(addresses)
    if addresses != _ACTIVE_IPV4 or relay != _ACTIVE_RELAY:
        _ACTIVE_IPV4, _ACTIVE_RELAY = addresses, relay
        invalidate_hosts_catalog_cache()
    return addresses


def learn_relay(addresses: tuple[str, ...], *, cancel=None, query=None) -> tuple[str, ...] | None:
    """Адрес посредника: вопрос ``RELAY_NAME`` к самому серверу по DoH (сертификат — для имени сервера).

    () — сервер ответил, что посредника нет; None — сервер не ответил ни по одному адресу.
    Ходит в сеть: вызывать только с пропуском шлюза проб.
    """
    from dns.custom_servers import parse_doh_template
    from utils.dns_wire import TYPE_A, query_doh

    query = query_doh if query is None else query
    template = parse_doh_template(zapret_dns.DOH_URL)
    for address in addresses:
        result = query(address, zapret_dns.RELAY_NAME, TYPE_A, tls_host=template.host, port=template.port,
                       path=template.path, timeout_s=RELAY_TIMEOUT_S, cancel=cancel)
        if getattr(result, "answered", False):
            return _global_ipv4(result.values(TYPE_A))
    return None


def refresh(*, cancel=None, lookup=None, relay_query=None, now: float | None = None) -> ServerChange:
    """Спрашивает адрес имени сервера и адрес его посредника; проверенный и другой делает нынешним.

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
    old_relay = _global_ipv4(before.get("relay_ipv4"))
    relay = learn_relay(learned, cancel=cancel, query=relay_query)
    relay = old_relay if relay is None else relay  # сервер промолчал — прежний адрес посредника остаётся
    moved, relay_changed = learned != current, relay != old_relay
    former = _global_ipv4(before.get("former_ipv4"))
    if moved:
        former = tuple(address for address in (*current, *former) if address not in learned)
    store.set_zapret_dns_server(
        ipv4=list(learned),
        former_ipv4=list(dict.fromkeys(former))[:MAX_LEARNED_HISTORY],
        checked_at=time.time() if now is None else now,
        relay_ipv4=list(relay),
    )
    if moved:
        log(f"Zapret DNS: сервер переехал: {', '.join(current)} -> {', '.join(learned)}", "INFO")
    if relay_changed:
        # Сам адрес посредника в журнал не пишется: он только для внутреннего употребления.
        log(f"Zapret DNS: посредник {('узнан' if not old_relay else 'сменил адрес') if relay else 'выключен'}", "INFO")
    if moved or relay_changed:
        adopt()
    detail = "сервер переехал" if moved else ("сменился посредник" if relay_changed else "адрес не изменился")
    return ServerChange(learned, moved, detail, relay_changed)
