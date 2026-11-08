"""Сервер очереди обновлений как «телефонная книга» соседей по интернету.

Программа и так ходит к серверу очереди обновлений, поэтому он годится как один
из трёх источников соседей (после книги на диске и обмена адресами). Две
просьбы, обе по HTTPS и без прокси Windows:

- ``POST <база>/peers?product=…&version=…&port=…`` — «я раздаю эту версию на
  таком порту»; сервер сам проверяет, что до порта можно достучаться, и только
  тогда записывает адрес, с которого пришла просьба;
- ``GET <база>/peers?product=…&version=…&limit=…`` — «кто раздаёт эту версию».

Ответ сервера не считается правдой: адреса проходят ту же проверку, что и все
остальные (``share_peers.clean_peer``), а файлы — проверку по подписанному списку.

Только стандартная библиотека.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence

from .share_peers import MAX_FILE_BYTES, parse_peer_list

TIMEOUT = 5.0


def _open(url: str, method: str, timeout: float):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "ZapretShare/1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def _url(base: str, **query: object) -> str:
    return f"{base.rstrip('/')}/peers?{urllib.parse.urlencode(query)}"


def register(
    bases: Sequence[str], product: str, version: str, port: int, *, timeout: float = TIMEOUT, opener: Callable = _open
) -> bool:
    """Записаться на всех известных серверах (основной и зеркала); ``True`` — принял хотя бы один.

    Записываемся везде, а не на первом ответившем: у каждого сервера своя книга, и
    блокировка одного не должна прятать раздающего от тех, кто спросит другой.
    """
    accepted = False
    for base in bases:
        try:
            with opener(_url(base, product=product, version=version, port=int(port)), "POST", timeout) as reply:
                accepted = accepted or getattr(reply, "status", 200) == 200
        except Exception:  # noqa: BLE001 — не вышло здесь, пробуем следующий сервер
            continue
    return accepted


WHY_CODES = frozenset({
    "via_upnp", "via_pcp", "via_natpmp",  # получилось, и чем именно
    "no_router", "no_local_ip", "no_external_ip", "no_answer", "refused", "cgnat", "double_nat", "not_public", "other",
})


def report_why(
    bases: Sequence[str], product: str, version: str, code: str, *, timeout: float = TIMEOUT, opener: Callable = _open
) -> bool:
    """Сообщить серверу одно слово: почему раздача по интернету не открылась (или чем открылась).

    Это счётчик для разработчиков, чтобы понять, чего людям не хватает чаще (UPnP, белого адреса).
    Уходит только пока раздача по интернету включена; в слове нет адреса, имени или идентификатора.
    Сервер считает слово один раз в сутки с одного адреса, не запоминая сам адрес.
    """
    if code not in WHY_CODES:
        return False
    accepted = False
    for base in bases:
        try:
            with opener(_url(base, product=product, version=version, why=code), "POST", timeout) as reply:
                accepted = accepted or getattr(reply, "status", 200) == 200
        except Exception:  # noqa: BLE001 — счётчик не обязателен
            continue
    return accepted


def lookup(
    bases: Sequence[str], product: str, version: str, limit: int = 16, *, timeout: float = TIMEOUT, opener: Callable = _open,
    allow_local: bool = False,
) -> list[tuple[str, int]]:
    """Кто раздаёт эту версию: списки всех ответивших серверов, без повторов, до ``limit`` адресов."""
    found: list[tuple[str, int]] = []
    for base in bases:
        if len(found) >= limit:
            break
        try:
            with opener(_url(base, product=product, version=version, limit=int(limit)), "GET", timeout) as reply:
                if getattr(reply, "status", 200) != 200:
                    continue
                for peer in parse_peer_list(reply.read(MAX_FILE_BYTES + 1), allow_local=allow_local):
                    if peer not in found:
                        found.append(peer)
        except Exception:  # noqa: BLE001
            continue
    return found[: int(limit)]


__all__ = ["WHY_CODES", "lookup", "register", "report_why"]
