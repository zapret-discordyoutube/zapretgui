"""Раздача файлов установленной версии другим копиям программы.

Один путь: ``GET /zapret-share/1/file/<sha256>``. Имя файла на диске клиент
не присылает и не может прислать: сервер сам ищет контрольную сумму среди
файлов своего подписанного списка выпуска. Поэтому через него нельзя прочитать
ничего, кроме файлов программы, и нельзя выйти из папки программы.

Дополнительно:

- чужие адреса не обслуживаются (``admits``): в локальной сети — только
  частные адреса и адреса канала; для интернета политику задаёт вызывающий;
- одновременно отдаётся не больше ``MAX_STREAMS`` файлов, остальным ``503``:
  раздача не должна съедать канал человека;
- файл отдаётся, только если на диске он ещё тот, что в списке (проверка по
  кэшу контрольных сумм: размер + время изменения), иначе ``404``;
- есть докачка ``Range: bytes=N-``.

Только стандартная библиотека.
"""

from __future__ import annotations

import http.server
import ipaddress
import re
import ssl
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO

from .manifest import FileEntry, safe_relative_path
from .plan import HashCache
from .share_manifest import MANIFEST_ROUTE, encode_manifest_reply
from .share_peers import PEERS_ROUTE, encode_peer_list

ROUTE = "/zapret-share/1/file/"
MAX_STREAMS = 2
CHUNK = 256 * 1024
_HEX64 = re.compile(r"[0-9a-f]{64}")
_RANGE = re.compile(r"bytes=(\d+)-")


def is_lan_address(text: str, *, allow_loopback: bool = False) -> bool:
    """Адрес из локальной сети: частный или адрес канала, но не интернет."""
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return allow_loopback
    return address.is_private or address.is_link_local


class InstalledFiles:
    """Файлы установленной версии по контрольной сумме."""

    def __init__(self, root: Path, entries: Iterable[FileEntry], hashes: HashCache | None = None) -> None:
        self._root = Path(root)
        self._hashes = hashes or HashCache()
        self._by_sha: dict[str, FileEntry] = {}
        for entry in entries:
            self._by_sha.setdefault(entry.sha256, entry)

    def __contains__(self, sha256: str) -> bool:
        return sha256 in self._by_sha

    def name_of(self, sha256: str) -> str:
        """Путь файла из подписанного списка (для журнала); чужого имени сюда не попадает."""
        entry = self._by_sha.get(sha256)
        return entry.path if entry is not None else ""

    def open(self, sha256: str) -> tuple[BinaryIO, int] | None:
        """Открытый файл и его размер, если он есть и не изменился."""
        entry = self._by_sha.get(sha256)
        if entry is None:
            return None
        path = self._root.joinpath(*safe_relative_path(entry.path).split("/"))
        try:
            if path.stat().st_size != entry.size or self._hashes.sha256(path) != sha256:
                return None
            return open(path, "rb"), entry.size
        except OSError:
            return None


class _Handler(http.server.BaseHTTPRequestHandler):
    server: ShareServer
    protocol_version = "HTTP/1.0"
    timeout = 15  # молчащее соединение не держит поток вечно

    def log_message(self, *_args) -> None:  # тишина: раздача не пишет в консоль
        return

    def _refuse(self, code: int) -> None:
        self._result = {403: "refused", 404: "not_found", 416: "refused", 503: "busy"}.get(code, "failed")
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 — имя задано http.server
        self._result, self._what, self._detail, self._sent = "ok", "request", "", 0
        try:
            self._answer()
        finally:
            activity = self.server.activity
            if activity is not None:
                activity.add(
                    "in", self.server.via, self.client_address[0], self._what, self._detail, self._sent, self._result
                )

    def _answer(self) -> None:
        if not self.server.admits(self.client_address[0]):
            return self._refuse(403)
        path = self.path.split("?", 1)[0]
        if path == PEERS_ROUTE and self.server.peers_provider is not None:
            self._what = "peers"
            return self._send_peers()
        if path == MANIFEST_ROUTE and self.server.manifest_provider is not None:
            self._what = "manifest"
            return self._send_manifest()
        name = path[len(ROUTE):] if path.startswith(ROUTE) else ""
        if not _HEX64.fullmatch(name):
            return self._refuse(404)
        self._what, self._detail = "file", self.server.files.name_of(name)
        if not self.server.slots.acquire(blocking=False):
            return self._refuse(503)
        try:
            opened = self.server.files.open(name)
            if opened is None:
                return self._refuse(404)
            handle, size = opened
            with handle:
                self._send(handle, size)
        finally:
            self.server.slots.release()

    def _send_manifest(self) -> None:
        blob = self.server.manifest_provider()
        if not blob:
            return self._refuse(404)
        body = encode_manifest_reply(*blob)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self._sent = len(body)
        except OSError:
            self._result = "interrupted"

    def _send_peers(self) -> None:
        product, version = self.server.identity
        body = encode_peer_list(list(self.server.peers_provider()), product=product, version=version)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self._sent = len(body)
        except OSError:
            self._result = "interrupted"

    def _send(self, handle: BinaryIO, size: int) -> None:
        start = 0
        match = _RANGE.fullmatch(self.headers.get("Range", ""))
        if match:
            start = int(match.group(1))
            if start > size:
                return self._refuse(416)
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size - start))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.end_headers()
        handle.seek(start)
        try:
            while chunk := handle.read(CHUNK):
                self.wfile.write(chunk)
                self._sent += len(chunk)
        except OSError:
            self._result = "interrupted"  # получатель ушёл: это его право


class ShareServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        files: InstalledFiles,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        admits: Callable[[str], bool] | None = None,
        allow_loopback: bool = False,
        max_streams: int = MAX_STREAMS,
        ssl_context: ssl.SSLContext | None = None,
        peers_provider: Callable[[], Iterable[tuple[str, int]]] | None = None,
        identity: tuple[str, str] = ("", ""),
        manifest_provider: Callable[[], tuple[bytes, bytes] | None] | None = None,
        activity=None,
        via: str = "lan",
    ) -> None:
        self.activity = activity  # журнал раздачи: кто и что у нас брал
        self.via = via  # "lan" или "internet": подпись событий
        self.ssl_context = ssl_context
        self.peers_provider = peers_provider  # есть у раздачи по интернету: обмен адресами
        self.identity = identity  # (продукт, версия): соседу видно, нужны ли ему наши файлы
        self.manifest_provider = manifest_provider  # подписанный список своей версии: для соседей без доступа к серверам
        self.files = files
        self.admits = admits or (lambda address: is_lan_address(address, allow_loopback=allow_loopback))
        self.slots = threading.BoundedSemaphore(max(1, max_streams))
        super().__init__((host, port), _Handler)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def get_request(self):
        sock, address = super().get_request()
        if self.ssl_context is not None:
            # Рукопожатие делает поток соединения, а не цикл приёма: медленный клиент никого не держит.
            sock = self.ssl_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, address

    def handle_error(self, request, client_address) -> None:
        return  # обрыв или чужое рукопожатие — не повод писать в консоль

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, name="share-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


__all__ = ["MAX_STREAMS", "ROUTE", "InstalledFiles", "ShareServer", "is_lan_address"]
