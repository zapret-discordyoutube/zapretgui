from __future__ import annotations

"""Соседи по интернету: сервер очереди как книга адресов и поиск по книге, обмену адресами и серверу."""

import functools
import http.server
import json
import threading
import time
import urllib.parse

import pytest

from zapret_update import share_cert, share_internet, share_peers, share_registry, share_serve

PRODUCT, VERSION = "zapret-gui", "21.1.7.233"


class FakeRegistry(http.server.ThreadingHTTPServer):
    """Поддельный сервер очереди: запоминает запросы и отдаёт заранее заданный список."""

    daemon_threads = True

    def __init__(self, peers=(), status=200):
        self.peers, self.status, self.requests = list(peers), status, []
        super().__init__(("127.0.0.1", 0), _RegistryHandler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}/stats"


class _RegistryHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _reply(self, body: bytes):
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        self.server.requests.append(("POST", self.path))
        self._reply(b'{"ok":true}')

    def do_GET(self):  # noqa: N802
        self.server.requests.append(("GET", self.path))
        self._reply(json.dumps({"peers": [list(p) for p in self.server.peers]}).encode())


@pytest.fixture
def registry():
    made = []

    def make(**kw):
        item = FakeRegistry(**kw)
        made.append(item)
        return item

    yield make
    for item in made:
        item.shutdown()
        item.server_close()


def test_register_sends_product_version_and_port_to_every_server(registry) -> None:
    first, second = registry(), registry()
    assert share_registry.register([first.base, second.base], PRODUCT, VERSION, 45123) is True
    method, path = first.requests[0]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    assert method == "POST" and path.startswith("/stats/peers?")
    assert query == {"product": [PRODUCT], "version": [VERSION], "port": ["45123"]}
    assert second.requests and second.requests[0][0] == "POST"  # записались и там: блокировка одного сервера не прячет нас


def test_register_moves_on_from_a_dead_or_unhappy_server(registry) -> None:
    unhappy, good = registry(status=503), registry()
    assert share_registry.register(["http://127.0.0.1:9/dead", unhappy.base, good.base], PRODUCT, VERSION, 45123, timeout=1) is True
    assert unhappy.requests and good.requests
    assert share_registry.register(["http://127.0.0.1:9/dead"], PRODUCT, VERSION, 45123, timeout=1) is False
    assert share_registry.register([], PRODUCT, VERSION, 45123) is False


def test_why_report_carries_only_the_word_and_unknown_words_never_leave(registry) -> None:
    server = registry()
    assert share_registry.report_why([server.base], PRODUCT, VERSION, "no_router") is True
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(server.requests[0][1]).query)
    assert server.requests[0][0] == "POST" and query == {"product": [PRODUCT], "version": [VERSION], "why": ["no_router"]}
    assert share_registry.report_why([server.base], PRODUCT, VERSION, "my address is 1.2.3.4") is False
    assert len(server.requests) == 1  # слово не из списка в сеть не уходит
    assert share_registry.report_why(["http://127.0.0.1:9/dead"], PRODUCT, VERSION, "other", timeout=1) is False


def test_lookup_returns_only_honest_global_addresses(registry) -> None:
    server = registry(peers=[("93.184.216.34", 45000), ("192.168.1.2", 45000), ("93.184.216.34", 80), ("93.184.216.35", 45001)])
    assert share_registry.lookup([server.base], PRODUCT, VERSION, 8) == [("93.184.216.34", 45000), ("93.184.216.35", 45001)]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(server.requests[0][1]).query)
    assert query["limit"] == ["8"] and query["version"] == [VERSION]


def test_lookup_merges_the_lists_of_all_servers_without_repeats_up_to_the_limit(registry) -> None:
    one = registry(peers=[("93.184.216.1", 45000), ("93.184.216.2", 45000)])
    two = registry(peers=[("93.184.216.2", 45000), ("93.184.216.3", 45000), ("93.184.216.4", 45000)])
    merged = share_registry.lookup([one.base, "http://127.0.0.1:9/dead", two.base], PRODUCT, VERSION, 10, timeout=1)
    assert merged == [("93.184.216.1", 45000), ("93.184.216.2", 45000), ("93.184.216.3", 45000), ("93.184.216.4", 45000)]
    assert len(share_registry.lookup([one.base, two.base], PRODUCT, VERSION, 3)) == 3


def test_lookup_of_a_broken_server_is_empty(registry) -> None:
    assert share_registry.lookup([registry(status=500).base], PRODUCT, VERSION) == []
    assert share_registry.lookup(["http://127.0.0.1:9/dead"], PRODUCT, VERSION, timeout=1) == []


# ── поиск соседей ──────────────────────────────────────────────────────

def _peer(tmp_path, name, *, version=VERSION, shares=()):
    root = tmp_path / name
    root.mkdir()
    server = share_serve.ShareServer(
        share_serve.InstalledFiles(root, []), host="127.0.0.1", allow_loopback=True,
        ssl_context=share_cert.server_context(), peers_provider=lambda: list(shares), identity=(PRODUCT, version),
    )
    server.start()
    return server


@pytest.fixture
def peers(tmp_path):
    made = []

    def make(name, **kw):
        server = _peer(tmp_path, name, **kw)
        made.append(server)
        return server

    yield make
    for server in made:
        server.stop()


PROBE = functools.partial(share_internet.probe, allow_local=True)


def _book(*servers):
    book = share_peers.PeerBook(allow_local=True)
    for server in servers:
        book.add("127.0.0.1", server.port)
    return book


def test_live_peers_with_the_right_version_are_returned_and_marked_good(peers) -> None:
    one, two = peers("one"), peers("two")
    book = _book(one, two)
    found = share_internet.find_internet_neighbours(book, PRODUCT, VERSION, probe_fn=PROBE)
    assert sorted(item.port for item in found) == sorted([one.port, two.port]) and all(item.tls for item in found)
    assert len(book.sample()) == 2


def test_peer_with_another_version_is_skipped_but_its_addresses_are_learned(peers) -> None:
    old = peers("old", version="21.1.7.200", shares=[("127.0.0.1", 45999)])
    book = _book(old)
    assert share_internet.find_internet_neighbours(book, PRODUCT, VERSION, probe_fn=PROBE) == []
    assert ("127.0.0.1", 45999) in book._items and len(book.sample()) == 1  # отвечает честно, просто не та версия


def test_peer_exchange_grows_the_book(peers) -> None:
    far = peers("far")
    near = peers("near", shares=[("127.0.0.1", far.port)])
    book = _book(near)
    share_internet.find_internet_neighbours(book, PRODUCT, VERSION, probe_fn=PROBE)
    assert ("127.0.0.1", far.port) in book._items


def test_dead_peer_collects_strikes_and_leaves_the_book(peers) -> None:
    alive = peers("alive")
    book = share_peers.PeerBook(allow_local=True)
    book.add("127.0.0.1", alive.port)
    book.add("127.0.0.1", 9)
    for _ in range(share_peers.STRIKES_TO_DROP):
        share_internet.find_internet_neighbours(book, PRODUCT, VERSION, probe_fn=functools.partial(share_internet.probe, allow_local=True, timeout=1))
    assert ("127.0.0.1", 9) not in book._items and ("127.0.0.1", alive.port) in book._items


def test_registry_is_asked_only_when_the_book_is_short_and_its_answer_goes_into_the_book(peers) -> None:
    server = peers("listed")
    asked = []

    def lookup(bases, product, version):
        asked.append((tuple(bases), product, version))
        return [("127.0.0.1", server.port)]

    book = share_peers.PeerBook(allow_local=True)
    found = share_internet.find_internet_neighbours(book, PRODUCT, VERSION, bases=["https://q.example"], probe_fn=PROBE, lookup_fn=lookup)
    assert asked == [(("https://q.example",), PRODUCT, VERSION)] and [item.port for item in found] == [server.port]
    full = share_peers.PeerBook(allow_local=True)
    for index in range(12):
        full.add("127.0.0.1", 46000 + index)
    asked.clear()
    share_internet.find_internet_neighbours(full, PRODUCT, VERSION, bases=["https://q.example"], probe_fn=lambda h, p: None, lookup_fn=lookup, budget=1)
    assert not asked


def test_nothing_known_and_nothing_to_ask_means_empty() -> None:
    assert share_internet.find_internet_neighbours(share_peers.PeerBook(allow_local=True), PRODUCT, VERSION) == []


def test_slow_peers_do_not_hold_the_search_beyond_its_budget() -> None:
    book = share_peers.PeerBook(allow_local=True)
    book.add("127.0.0.1", 46001)

    def slow(host, port):
        time.sleep(1.5)
        return ([], PRODUCT, VERSION)

    began = time.monotonic()
    assert share_internet.find_internet_neighbours(book, PRODUCT, VERSION, probe_fn=slow, budget=0.3) == []
    assert time.monotonic() - began < 1.0


def test_search_stops_at_the_wanted_count(peers) -> None:
    servers = [peers(f"p{i}") for i in range(4)]
    found = share_internet.find_internet_neighbours(_book(*servers), PRODUCT, VERSION, probe_fn=PROBE, want=2)
    assert len(found) == 2


def test_search_for_any_version_accepts_any_version_of_the_product_and_asks_the_server_with_a_wildcard(peers) -> None:
    old = peers("old", version="21.1.7.200")
    new = peers("new", version="21.1.7.240")
    asked = []

    def lookup(bases, product, version):
        asked.append(version)
        return []

    book = share_peers.PeerBook(allow_local=True)
    book.add("127.0.0.1", old.port)
    found = share_internet.find_internet_neighbours(book, PRODUCT, None, bases=["https://q.example"], probe_fn=PROBE, lookup_fn=lookup)
    assert sorted(item.port for item in found) == [old.port]  # версия не важна: важно, что это наша программа
    book.add("127.0.0.1", new.port)
    found = share_internet.find_internet_neighbours(book, PRODUCT, None, probe_fn=PROBE)
    assert sorted(item.port for item in found) == sorted([old.port, new.port])
    assert asked == ["*"]
