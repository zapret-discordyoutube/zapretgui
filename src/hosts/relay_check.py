"""Отвечает ли сервер-посредник профиля из сети пользователя.

Профиль в «Редакторе hosts» направляет имена сервиса на адрес посредника. Если
провайдер заблокировал этот адрес, запись в hosts не откроет сервис, а сломает
его: сайт перестанет открываться совсем. Поэтому перед записью программа
пробует соединиться с каждым посредником и не пишет строки, если он молчит.

Посредником считается адрес, на который ведут сразу несколько имён: настоящий
адрес сайта обычно стоит у одного-двух имён, а посредник — у всего сервиса.
"""

from __future__ import annotations

import socket
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

# На столько имён должен вести адрес, чтобы считаться посредником.
RELAY_MIN_NAMES = 3
CONNECT_TIMEOUT_S = 3.0
RELAY_PORT = 443


def relay_addresses(rows: list[tuple[str, str]]) -> list[str]:
    counts = Counter(str(ip or "").strip() for _domain, ip in rows)
    return [ip for ip, count in counts.items() if ip and count >= RELAY_MIN_NAMES]


def _connects(address: str) -> bool:
    try:
        with socket.create_connection((address, RELAY_PORT), timeout=CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


def unreachable_relays(rows: list[tuple[str, str]]) -> list[str]:
    """Посредники из строк hosts, с которыми не удалось соединиться."""
    relays = relay_addresses(rows)
    if not relays:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(relays)), thread_name_prefix="hosts-relay") as pool:
        alive = list(pool.map(_connects, relays))
    return [address for address, ok in zip(relays, alive) if not ok]


__all__ = ["relay_addresses", "unreachable_relays"]
