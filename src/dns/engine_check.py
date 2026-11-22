"""Принял бы встроенный шифрованный DNS (dnscrypt-proxy) этот сервер: тот же пробный запрос, что и у движка.

Прежде чем взять сервер DoH в работу, движок задаёт ему один вопрос: запись NS
для случайного несуществующего имени ``<16 букв>.test.dnscrypt``. Ответ обязан
быть «такого имени нет» (NXDOMAIN). Любой другой код движок записывает как
«may be a lying resolver -- skipping» и сервер не берёт. Сам запрос, по
исходникам движка (``serversInfo.go``: ``dohNXTestPacket``, ``fetchDoHServerInfo``),
так и построен.

Есть серверы, что на такой вопрос отвечают иначе: Malw.link — пустым успешным
ответом (NOERROR без записей), GeoHide — отказом (REFUSED). Для обычных запросов
они исправны, но движок их отвергает, и Windows 10 ставит такой сервер адресами,
без шифрования (замер на win10, 2026-11-22). Причина — в ответе сервера, а не в
сети человека: «шифрованный путь закрыт» здесь было бы неправдой.

Проверка нужна только затем, чтобы сказать человеку настоящую причину; ставит
сервер или нет — по-прежнему решает ответ самого движка.
"""

from __future__ import annotations

import random
import string
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from utils.dns_wire import STATUS_NXDOMAIN, STATUS_TIMEOUT, STATUS_ERROR, TYPE_NS, query_doh

# Сколько адресов спрашиваем: у плитки их не больше четырёх.
MAX_ADDRESSES = 4
TIMEOUT_S = 4.0


def probe_name() -> str:
    """Случайное несуществующее имя той же формы, что у движка."""
    return "".join(random.choice(string.ascii_lowercase) for _ in range(16)) + ".test.dnscrypt"


def engine_rejects(
    doh_url: str,
    addresses: Iterable[str],
    *,
    query: Callable[..., object] = query_doh,
) -> bool:
    """True: сервер отвечает, но на пробный вопрос движка не «имени нет», и ни один адрес не ответил как надо.

    False — движок принял бы сервер (хотя бы один адрес ответил NXDOMAIN) или проверить не вышло
    (адреса молчат: тогда причина другая, и говорить про ответ сервера нельзя).
    """
    parts = urlsplit(str(doh_url or "").strip())
    host = parts.hostname or ""
    found = [str(item).strip() for item in addresses or () if str(item or "").strip() and ":" not in str(item)]
    found = list(dict.fromkeys(found))[:MAX_ADDRESSES]
    if parts.scheme != "https" or not host or not found:
        return False

    def ask(address: str):
        try:
            return query(
                address,
                probe_name(),
                TYPE_NS,
                tls_host=host,
                port=parts.port or 443,
                path=parts.path or "/dns-query",
                timeout_s=TIMEOUT_S,
            )
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=len(found)) as pool:
        results = list(pool.map(ask, found))
    statuses = [str(getattr(item, "status", "")) for item in results if item is not None]
    if STATUS_NXDOMAIN in statuses:
        return False
    answered = [item for item in statuses if item and item not in (STATUS_TIMEOUT, STATUS_ERROR)]
    return bool(answered)
