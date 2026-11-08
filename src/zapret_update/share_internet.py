"""Поиск соседей по интернету: книга, обмен адресами, сервер очереди.

Порядок, пока не наберётся нужное число живых соседей с нужной версией:

1. адреса из книги на диске (``PeerBook.pick``);
2. если мало — просим сервер очереди назвать раздающих эту версию;
3. каждого кандидата проверяем запросом обмена адресами по TLS: он отвечает
   списком других (они идут в книгу), продуктом и версией. Нужной версии у него
   нет — значит, для нас он бесполезен; не ответил — штраф в книге.

Проверка идёт параллельно с коротким сроком: поиск не должен задерживать обычное
обновление с серверов, поэтому у всего поиска общий бюджет времени.

Только стандартная библиотека.
"""

from __future__ import annotations

import http.client
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from . import share_registry
from .share_cert import client_context, server_context
from .share_portmap import PortMapper
from .share_serve import InstalledFiles, ShareServer
from .share_discover import Neighbour
from .share_peers import MAX_SHARED, PEERS_ROUTE, PeerBook, parse_peer_reply

WANT = 6
PROBE_TIMEOUT = 3.0
BUDGET = 5.0


def probe(host: str, port: int, *, timeout: float = PROBE_TIMEOUT, allow_local: bool = False):
    """Обмен адресами с одним соседом: ``(адреса, продукт, версия)`` или ``None``, если не ответил."""
    connection = http.client.HTTPSConnection(host, port, timeout=timeout, context=client_context())
    try:
        connection.request("GET", PEERS_ROUTE, headers={"User-Agent": "ZapretShare/1"})
        reply = connection.getresponse()
        if reply.status != 200:
            return None
        return parse_peer_reply(reply.read(65 * 1024), allow_local=allow_local)
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return None
    finally:
        connection.close()


def find_internet_neighbours(
    book: PeerBook,
    product: str,
    version: str | None,
    *,
    bases: Sequence[str] = (),
    want: int = WANT,
    budget: float = BUDGET,
    probe_fn: Callable = probe,
    lookup_fn: Callable = share_registry.lookup,
) -> list[Neighbour]:
    """Живые соседи по интернету с нужной версией (до ``want``); книга обновляется по ходу."""
    deadline = time.monotonic() + budget
    candidates = book.pick(want * 2)
    if len(candidates) < want * 2 and bases:
        for host, port in lookup_fn(bases, product, version or "*"):
            book.add(host, port)
        candidates = book.pick(want * 2)
    if not candidates:
        return []
    found: list[Neighbour] = []
    pool = ThreadPoolExecutor(max_workers=4)
    try:
        futures = {pool.submit(probe_fn, host, port): (host, port) for host, port in candidates}
        pending = set(futures)
        while pending and len(found) < want and (left := deadline - time.monotonic()) > 0:
            done, pending = wait(pending, timeout=left, return_when="FIRST_COMPLETED")
            for future in done:
                host, port = futures[future]
                try:
                    answer = future.result()
                except Exception:  # noqa: BLE001
                    answer = None
                if answer is None:
                    book.bad(host, port)
                    continue
                book.good(host, port)
                peers, their_product, their_version = answer
                for other in peers[:MAX_SHARED]:
                    book.add(*other)
                if their_product == product and (version is None or their_version == version) and len(found) < want:
                    found.append(Neighbour(host, port, tls=True))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return found


HEARTBEAT_SECONDS = 20 * 60


class InternetSide:
    """Раздача по интернету: TLS-сервер файлов, проброс порта на роутере, запись на сервере очереди.

    Поднимается только если роутер открыл порт и его внешний адрес «белый»
    (``PortMapper``): иначе снаружи не достучаться, и раздавать нечего.
    """

    def __init__(
        self,
        *,
        product: str,
        book: PeerBook,
        bases: Callable[[], Sequence[str]] = lambda: (),
        open_firewall: Callable[[int], bool] | None = None,
        mapper_factory: Callable[[int], object] = PortMapper,
        register_fn: Callable = share_registry.register,
        tls_factory: Callable[[], ssl.SSLContext] = server_context,
        host: str = "0.0.0.0",
        heartbeat: float = HEARTBEAT_SECONDS,
        report: Callable[[str], None] = lambda _text: None,
        allow_loopback: bool = False,
    ) -> None:
        self._product, self._book, self._bases = product, book, bases
        self._open_firewall, self._mapper_factory, self._register = open_firewall, mapper_factory, register_fn
        self._tls_factory, self._host, self._heartbeat, self._report_out = tls_factory, host, heartbeat, report
        self.notes: deque[tuple[float, str]] = deque(maxlen=6)  # что и почему не вышло: видно на странице раздачи
        self._allow_loopback = allow_loopback
        self._server: ShareServer | None = None
        self._mapper = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.mapping = None
        self.version = ""

    def _report(self, text: str) -> None:
        self.notes.append((time.time(), str(text)))
        self._report_out(text)

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int | None:
        return self._server.port if self._server is not None else None

    def start(self, files: InstalledFiles, version: str, manifest_provider=None, activity=None) -> bool:
        server = ShareServer(
            files, host=self._host, ssl_context=self._tls_factory(), peers_provider=self._book.sample,
            identity=(self._product, version), admits=lambda _address: True, manifest_provider=manifest_provider,
            activity=activity, via="internet",
        )
        server.start()  # сразу: остановка сервера, который не запускали, зависла бы
        try:
            if self._open_firewall is not None and not self._open_firewall(server.port):
                self._report("Раздача по интернету: правило брандмауэра не добавилось, работаем без него")
            mapper = self._mapper_factory(server.port)
            mapping = mapper.start()
            if mapping is None:
                reason = getattr(mapper, "reason", "") or "роутер не открыл порт или у вас нет «белого» адреса"
                self._report(f"Раздача по интернету недоступна: {reason}")
                server.stop()
                return False
        except Exception:  # noqa: BLE001
            server.stop()
            raise
        self._server, self._mapper, self.mapping, self.version = server, mapper, mapping, version
        self._stop.clear()
        self._thread = threading.Thread(target=self._keep_registered, name="share-registry", daemon=True)
        self._thread.start()
        self._report(f"Раздача по интернету включена: {mapping.external_ip}:{mapping.external_port}")
        return True

    def _keep_registered(self) -> None:
        while not self._stop.is_set():
            bases = list(self._bases())
            if bases and self.mapping is not None:
                try:
                    self._register(bases, self._product, self.version, self.mapping.external_port)
                except Exception:  # noqa: BLE001 — запись на сервере не обязательна
                    pass
            if self._stop.wait(self._heartbeat):
                return

    def stop(self) -> None:
        self._stop.set()
        server, mapper = self._server, self._mapper
        self._server = self._mapper = self.mapping = None
        for part in (mapper, server):
            if part is not None:
                try:
                    part.stop()
                except Exception:  # noqa: BLE001
                    pass
        try:
            self._book.save()
        except OSError:
            pass


__all__ = ["BUDGET", "HEARTBEAT_SECONDS", "WANT", "InternetSide", "find_internet_neighbours", "probe"]
