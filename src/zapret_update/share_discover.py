"""Поиск соседей по локальной сети: «у кого уже есть версия X?».

Обновляющийся компьютер шлёт короткий UDP-вопрос всем в сети (широковещание).
Компьютер, у которого установлена именно эта версия этого продукта, отвечает
ему лично: «я здесь, файлы на порту N». Файлы потом берутся по HTTP у
``serve.ShareServer`` (проверка каждого файла — по подписанному списку).

Правила, чтобы это не стало дырой:

- пакеты короче ``MAX_PACKET`` байт и только в формате ниже; мусор молча
  отбрасывается, ответа на него нет;
- отвечают и принимаются ответы только от адресов локальной сети;
- отвечает только тот, у кого версия и продукт совпали буква в букву;
- порт в ответе должен быть настоящим (1024–65535);
- ответов не больше ``MAX_NEIGHBOURS``, повторы убираются;
- один ответчик отвечает не чаще ``REPLIES_PER_SECOND`` раз в секунду.

Только стандартная библиотека.
"""

from __future__ import annotations

import json
import selectors
import socket
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .share_serve import is_lan_address

MAGIC = "zapret-share"
PROTOCOL = 1
# У каждого продукта свой порт: Zapret GUI и Zapret KVN на одном компьютере
# не мешают друг другу.
DISCOVERY_PORT_GUI = 47811
DISCOVERY_PORT_KVN = 47812
MAX_PACKET = 512
MAX_NEIGHBOURS = 8
REPLIES_PER_SECOND = 20
FIND_TIMEOUT = 1.5
# После первого ответа остальные успевают за это время: ждать весь срок не нужно.
SETTLE_AFTER_FIRST = 0.3


@dataclass(frozen=True, slots=True)
class Neighbour:
    host: str
    port: int
    tls: bool = False  # соседи по интернету говорят по TLS, по локальной сети — по обычному HTTP


def _pack(**fields) -> bytes:
    return json.dumps({"z": MAGIC, "v": PROTOCOL, **fields}, separators=(",", ":")).encode("utf-8")


def _unpack(data: bytes) -> dict | None:
    if not data or len(data) > MAX_PACKET:
        return None
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(message, dict) or message.get("z") != MAGIC or message.get("v") != PROTOCOL:
        return None
    return message


def _same(message: dict, product: str, version: str) -> bool:
    return message.get("product") == product and message.get("version") == version


def local_lan_addresses() -> list[str]:
    """Свои адреса в локальных сетях: с каждого уходит свой вопрос."""
    found: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(str(item[4][0]))
    except OSError:
        pass
    try:  # адрес, с которого уходят в интернет (UDP «connect» ничего не шлёт)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(str(probe.getsockname()[0]))
    except OSError:
        pass
    return sorted(address for address in found if is_lan_address(address))


class Responder:
    """Отвечает соседям, что у нас есть эта версия и где брать файлы."""

    def __init__(
        self,
        product: str,
        version: str,
        http_port: int,
        *,
        host: str = "0.0.0.0",
        port: int = DISCOVERY_PORT_GUI,
        admits: Callable[[str], bool] | None = None,
        allow_loopback: bool = False,
        activity=None,
    ) -> None:
        self._activity = activity  # журнал раздачи: кто спрашивал «у кого есть версия?»
        self._product, self._version, self._http_port = product, version, int(http_port)
        self._admits = admits or (lambda address: is_lan_address(address, allow_loopback=allow_loopback))
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, port))
        self._socket.settimeout(0.5)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._window = (0.0, 0)

    @property
    def port(self) -> int:
        return int(self._socket.getsockname()[1])

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="share-responder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._socket.close()

    def _allowed_now(self) -> bool:
        now = time.monotonic()
        started, count = self._window
        if now - started >= 1.0:
            self._window = (now, 1)
            return True
        if count >= REPLIES_PER_SECOND:
            return False
        self._window = (started, count + 1)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, sender = self._socket.recvfrom(MAX_PACKET + 1)
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                self._answer(data, sender)
            except OSError:
                pass  # получатель пропал: это не наша забота

    def _answer(self, data: bytes, sender: tuple) -> None:
        message = _unpack(data)
        if message is None or message.get("op") != "who" or not _same(message, self._product, self._version):
            return
        if not self._admits(sender[0]) or not self._allowed_now():
            return
        self._socket.sendto(
            _pack(op="here", product=self._product, version=self._version, port=self._http_port), sender
        )
        if self._activity is not None:
            self._activity.add("in", "lan", sender[0], "search")


def find_neighbours(
    product: str,
    version: str,
    *,
    timeout: float = FIND_TIMEOUT,
    port: int = DISCOVERY_PORT_GUI,
    targets: Iterable[tuple[str, int]] | None = None,
    sources: Iterable[str] | None = None,
    allow_loopback: bool = False,
    limit: int = MAX_NEIGHBOURS,
) -> list[Neighbour]:
    """Соседи с такой же версией: ждёт ответы ``timeout`` секунд.

    ``targets`` и ``sources`` нужны тестам; по умолчанию вопрос идёт всем в
    сети (``255.255.255.255``) с каждого своего адреса локальной сети — иначе
    на компьютере с несколькими сетевыми картами он ушёл бы только в одну.
    """
    destinations = list(targets) if targets is not None else [("255.255.255.255", port)]
    origins = list(sources) if sources is not None else [*local_lan_addresses(), ""]
    query = _pack(op="who", product=product, version=version)
    selector = selectors.DefaultSelector()
    sockets: list[socket.socket] = []
    try:
        for origin in origins:
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sender)
            try:
                sender.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sender.bind((origin, 0))
                sender.setblocking(False)
                for destination in destinations:
                    sender.sendto(query, destination)
                selector.register(sender, selectors.EVENT_READ)
            except OSError:
                continue  # этой сети сейчас нет: остальные работают
        found: dict[tuple[str, int], Neighbour] = {}
        deadline = time.monotonic() + max(timeout, 0.0)
        while len(found) < limit and (left := deadline - time.monotonic()) > 0:
            for key, _events in selector.select(left):
                try:
                    data, address = key.fileobj.recvfrom(MAX_PACKET + 1)
                except OSError:
                    continue
                neighbour = _accept(data, address[0], product, version, allow_loopback)
                if neighbour is not None:
                    found.setdefault((neighbour.host, neighbour.port), neighbour)
                    deadline = min(deadline, time.monotonic() + SETTLE_AFTER_FIRST)
        return list(found.values())[:limit]
    finally:
        selector.close()
        for sender in sockets:
            sender.close()


def _accept(data: bytes, host: str, product: str, version: str, allow_loopback: bool) -> Neighbour | None:
    message = _unpack(data)
    if message is None or message.get("op") != "here" or not _same(message, product, version):
        return None
    if not is_lan_address(host, allow_loopback=allow_loopback):
        return None
    port = message.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        return None
    return Neighbour(host, port)


__all__ = [
    "DISCOVERY_PORT_GUI",
    "DISCOVERY_PORT_KVN",
    "FIND_TIMEOUT",
    "MAX_NEIGHBOURS",
    "SETTLE_AFTER_FIRST",
    "Neighbour",
    "Responder",
    "find_neighbours",
    "local_lan_addresses",
]
