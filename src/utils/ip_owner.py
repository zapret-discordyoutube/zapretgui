"""Чья это сеть: владелец адреса по DNS-службе Team Cymru.

Служба отвечает обычными DNS-записями: без сайтов, ключей и ограничений по
числу запросов. Первый запрос даёт номер сети (автономной системы), диапазон
и страну, второй — название владельца этой сети.

Каким способом спрашивать, решает вызывающий (``ask``): проверка перехвата
DNS обязана идти шифрованным путём, иначе о владельце ответит сам перехватчик.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from utils.dns_wire import TYPE_TXT, DnsQueryResult, reverse_name

__all__ = ["IpOwner", "lookup_ip_owner"]

Ask = Callable[[str, int], "DnsQueryResult | None"]


@dataclass(frozen=True, slots=True)
class IpOwner:
    asn: str = ""
    prefix: str = ""
    country: str = ""
    owner: str = ""


def _texts(result: DnsQueryResult | None) -> tuple[str, ...]:
    return result.values(TYPE_TXT) if result is not None else ()


def lookup_ip_owner(ip: str, ask: Ask) -> IpOwner | None:
    """Владелец сети адреса или None, если служба не ответила."""
    try:
        pointer = reverse_name(ip)
    except ValueError:
        return None
    if pointer.endswith(".in-addr.arpa"):
        name = pointer[: -len(".in-addr.arpa")] + ".origin.asn.cymru.com"
    else:
        name = pointer[: -len(".ip6.arpa")] + ".origin6.asn.cymru.com"
    texts = _texts(ask(name, TYPE_TXT))
    if not texts:
        return None
    parts = [part.strip() for part in texts[0].split("|")]
    asn = parts[0].split()[0] if parts and parts[0] else ""
    info = IpOwner(
        asn=asn,
        prefix=parts[1] if len(parts) > 1 else "",
        country=parts[2] if len(parts) > 2 else "",
    )
    if not asn:
        return info
    owner_texts = _texts(ask(f"AS{asn}.asn.cymru.com", TYPE_TXT))
    if not owner_texts:
        return info
    return IpOwner(asn=info.asn, prefix=info.prefix, country=info.country, owner=owner_texts[0].split("|")[-1].strip())
