"""Что за адрес пришёл в ответе DNS: настоящий сервер, заглушка или локальный.

Единственное место, где программа решает, «похож ли адрес на подмену».
Проверки (BlockCheck, «Проверка домена», подбор стратегий) берут отсюда вид
адреса и сами решают, что сказать пользователю.
"""

from __future__ import annotations

from enum import Enum
from ipaddress import ip_address, ip_network

__all__ = [
    "AddressKind",
    "BLOCK_STUB_OWNERS",
    "STUB_KINDS",
    "address_kind",
    "block_stub_owner",
    "is_stub_address",
]


class AddressKind(Enum):
    PUBLIC = "public"
    # Известная страница-заглушка провайдера или РКН.
    BLOCK_STUB = "block_stub"
    # VPN-клиенты в режиме fake-ip (sing-box, Clash, Happ) отдают такие адреса
    # и сами подставляют настоящий сервер. Это не провайдер.
    FAKE_IP = "fake_ip"
    # Адрес самого компьютера или «никуда»: 127.0.0.1, 0.0.0.0, ::1.
    SELF = "self"
    # Домашняя или рабочая сеть.
    LOCAL = "local"
    # Внутренняя сеть провайдера (CGNAT, RFC 6598).
    CARRIER = "carrier"
    # Зарезервированные и групповые адреса.
    SERVICE = "service"
    INVALID = "invalid"


# Чья заглушка — для подписи в отчётах.
BLOCK_STUB_OWNERS: dict[str, str] = {
    "195.82.146.214": "Ростелеком",
    "81.19.72.32": "МТС",
    "213.180.193.250": "Билайн",
    "217.169.80.229": "Мегафон",
    "62.33.207.196": "РКН",
    "62.33.207.197": "РКН",
    "62.33.207.198": "РКН",
    "10.10.10.10": "внутренняя заглушка",
}

_FAKE_IP_NETWORK = ip_network("198.18.0.0/15")
_CARRIER_NETWORK = ip_network("100.64.0.0/10")

# У общедоступного сайта таких адресов не бывает: если DNS вернул такой адрес,
# ответ подменён. Fake-ip сюда не входит — это VPN пользователя.
STUB_KINDS = frozenset(
    {
        AddressKind.BLOCK_STUB,
        AddressKind.SELF,
        AddressKind.LOCAL,
        AddressKind.CARRIER,
        AddressKind.SERVICE,
    }
)


def address_kind(ip: str) -> AddressKind:
    text = str(ip or "").strip()
    if text in BLOCK_STUB_OWNERS:
        return AddressKind.BLOCK_STUB
    try:
        address = ip_address(text)
    except ValueError:
        return AddressKind.INVALID
    if address.version == 4 and address in _FAKE_IP_NETWORK:
        return AddressKind.FAKE_IP
    if address.is_loopback or address.is_unspecified:
        return AddressKind.SELF
    if address.version == 4 and address in _CARRIER_NETWORK:
        return AddressKind.CARRIER
    # Раньше проверки «частный»: Python относит зарезервированные сети к частным.
    if address.is_reserved or address.is_multicast:
        return AddressKind.SERVICE
    if address.is_link_local or address.is_private:
        return AddressKind.LOCAL
    return AddressKind.PUBLIC


def block_stub_owner(ip: str) -> str:
    return BLOCK_STUB_OWNERS.get(str(ip or "").strip(), "")


def is_stub_address(ip: str) -> bool:
    return address_kind(ip) in STUB_KINDS
