"""Где перехватывают обычные DNS-запросы.

Провайдер умеет отвечать на DNS-запрос сам, не пропуская его до сервера,
которому тот адресован. Место такого перехвата находится тем же приёмом, что
и место фильтра: срок жизни пакета (TTL).

Запрос к известному зарубежному серверу (``8.8.8.8``) уходит со сроком жизни
``k`` узлов. До Google он при малом ``k`` заведомо не доходит — пакет гибнет на
``k``-м узле. Если ответ всё же пришёл, ответил не Google, а кто-то на дороге,
не дальше ``k``-го узла. Наименьший такой ``k`` и есть место перехвата.
Расстояние до самого сервера берётся из трассировки: ответ при ``k``, равном
этому расстоянию, — честный ответ сервера, перехвата не видно.

Это место может не совпадать с местом фильтра по имени сайта: тогда устройств
на дороге не меньше двух. Способу не нужны особые права.

Сбор фактов (``collect``) отделён от вывода (``judge``).
"""

from __future__ import annotations

import os
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from utils.socket_cancel import SocketCancel, close_quietly

__all__ = ["DNS_SERVER", "DnsPlaceFacts", "DnsPlaceVerdict", "ask_with_ttl", "collect", "judge"]

DNS_SERVER = "8.8.8.8"
PROBE_NAME = "example.com"
MAX_TTL = 20
REPLY_TIMEOUT_S = 1.5

PLACE_FOUND = "found"
PLACE_NONE = "none"
PLACE_UNKNOWN = "unknown"


def _query(name: str, ident: int) -> bytes:
    labels = b"".join(bytes([len(part)]) + part.encode("ascii", "ignore") for part in name.strip(".").split("."))
    # Обычный запрос записи A с рекурсией.
    return struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + labels + b"\x00" + struct.pack(">HH", 1, 1)


def ask_with_ttl(
    server: str,
    ttl: int,
    *,
    name: str = PROBE_NAME,
    timeout: float = REPLY_TIMEOUT_S,
    cancel: SocketCancel | None = None,
) -> bool | None:
    """Пришёл ли ответ на DNS-запрос со сроком жизни ``ttl``. None — проверку сняли."""
    token = cancel or SocketCancel()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if not token.track(sock):
            return None
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, max(1, min(255, int(ttl))))
        sock.connect((server, 53))
        ident = int.from_bytes(os.urandom(2), "big")
        sock.send(_query(name, ident))
        deadline = time.perf_counter() + timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None if token.cancelled else False
            sock.settimeout(remaining)
            try:
                data = sock.recv(2048)
            except (socket.timeout, TimeoutError):
                return None if token.cancelled else False
            except OSError:
                # Узел сообщил, что пакет погиб: это не ответ DNS, ждём дальше.
                if token.cancelled:
                    return None
                continue
            if len(data) >= 12 and data[:2] == ident.to_bytes(2, "big") and data[2] & 0x80:
                return True
    except OSError:
        return None if token.cancelled else False
    finally:
        if sock is not None:
            token.release(sock)
            close_quietly(sock)


@dataclass(frozen=True, slots=True)
class DnsPlaceFacts:
    server: str
    # Сроки жизни, при которых пришёл ответ.
    answered: tuple[int, ...] = ()
    checked_up_to: int = 0
    # Сколько узлов до самого сервера по трассировке. 0 — неизвестно.
    distance: int = 0
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class DnsPlaceVerdict:
    code: str
    text: str
    # Перехват не дальше этого узла (между ``hop - 1`` и ``hop``).
    hop: int | None = None


def collect(
    ask: Callable[[int], bool | None], *, submit: Callable, distance: int = 0, server: str = DNS_SERVER, max_ttl: int = MAX_TTL
) -> DnsPlaceFacts:
    """``ask(срок жизни)`` → пришёл ли ответ. Все сроки жизни спрашиваются одновременно: это отдельные пакеты."""
    last = max(1, int(max_ttl))
    futures = [(ttl, submit(ask, ttl)) for ttl in range(1, last + 1)]
    answers = [(ttl, future.result()) for ttl, future in futures]
    cancelled = any(answer is None for _ttl, answer in answers)
    return DnsPlaceFacts(server, tuple(ttl for ttl, answer in answers if answer), last, int(distance or 0), cancelled)


def judge(facts: DnsPlaceFacts | None) -> DnsPlaceVerdict | None:
    """Где перехватывают DNS. None — проверку сняли."""
    if facts is None or facts.cancelled:
        return None
    if not facts.answered:
        return DnsPlaceVerdict(
            PLACE_UNKNOWN, f"на обычный DNS-запрос к {facts.server} ответа нет ни с каким сроком жизни — место не определить"
        )
    first = min(facts.answered)
    if not facts.distance:
        return DnsPlaceVerdict(
            PLACE_UNKNOWN,
            f"ответ на DNS-запрос к {facts.server} приходит начиная с {first}-го узла, но сколько узлов до самого "
            "сервера, узнать не удалось — перехват это или сам сервер, не различить",
        )
    if first < facts.distance:
        where = "не дальше первого узла — это ваш роутер" if first == 1 else f"между узлами {first - 1} и {first}"
        return DnsPlaceVerdict(
            PLACE_FOUND,
            f"обычные DNS-запросы перехватывают {where}: ответ приходит, когда пакету хватает срока жизни только "
            f"на {first} узл., а до {facts.server} их {facts.distance}",
            first,
        )
    return DnsPlaceVerdict(
        PLACE_NONE,
        f"перехвата обычных DNS-запросов по дороге не видно: {facts.server} отвечает сам, с {facts.distance}-го узла",
    )
