"""Отвечает ли сервер-посредник профиля из сети пользователя.

Профиль в «Редакторе hosts» направляет имена сервиса на адрес посредника. Если
провайдер заблокировал этот адрес, запись в hosts не откроет сервис, а сломает
его: сайт перестанет открываться совсем. Поэтому перед записью программа
пробует соединиться с каждым посредником и не пишет строки, если он молчит.

Какие адреса считать посредниками, решает каталог (`get_relay_addresses`): это
адреса, через которые профиль ведёт много сервисов сразу. Угадывать посредник по
числу имён нельзя: настоящий адрес сайта тоже бывает общим для десятка имён и
при этом из России не отвечает — так ошибались версии 21.1.7.116–119.
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor

CONNECT_TIMEOUT_S = 3.0
RELAY_PORT = 443


def relay_addresses(rows: list[tuple[str, str]], known_relays: set[str]) -> list[str]:
    """Посредники среди адресов строк hosts, по порядку первого появления."""
    known = {str(address).strip().casefold() for address in known_relays}
    found: list[str] = []
    for _domain, ip in rows:
        address = str(ip or "").strip()
        if address and address.casefold() in known and address not in found:
            found.append(address)
    return found


def _connects(address: str) -> bool:
    try:
        with socket.create_connection((address, RELAY_PORT), timeout=CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


def unreachable_relays(rows: list[tuple[str, str]], known_relays: set[str]) -> list[str]:
    """Посредники из строк hosts, с которыми не удалось соединиться."""
    relays = relay_addresses(rows, known_relays)
    if not relays:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(relays)), thread_name_prefix="hosts-relay") as pool:
        alive = list(pool.map(_connects, relays))
    return [address for address, ok in zip(relays, alive) if not ok]


__all__ = ["relay_addresses", "unreachable_relays"]
