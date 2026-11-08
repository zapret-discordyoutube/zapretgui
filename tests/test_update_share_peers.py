from __future__ import annotations

"""Адресная книга и обмен адресами: верят только «белым» адресам и честным ответам."""

import hashlib
import http.client
import json
import time

import pytest

from zapret_update import share_cert, share_peers, share_serve

GOOD = "93.184.216.34"


@pytest.mark.parametrize(
    ("host", "port", "ok"),
    [
        (GOOD, 45000, True),
        ("2606:2800:220:1:248:1893:25c8:1946", 45000, True),
        ("::ffff:93.184.216.34", 45000, True),
        ("192.168.1.5", 45000, False),
        ("10.0.0.1", 45000, False),
        ("127.0.0.1", 45000, False),
        ("100.64.0.9", 45000, False),
        ("169.254.1.1", 45000, False),
        ("0.0.0.0", 45000, False),
        ("localhost", 45000, False),
        (GOOD, 80, False),
        (GOOD, 1023, False),
        (GOOD, 65536, False),
        (GOOD, True, False),
        (GOOD, "45000", False),
        (None, 45000, False),
        (12345, 45000, False),
    ],
)
def test_only_global_addresses_with_a_real_port_are_taken(host, port, ok) -> None:
    assert (share_peers.clean_peer(host, port) is not None) is ok


def test_ipv4_mapped_ipv6_is_stored_as_plain_ipv4() -> None:
    assert share_peers.clean_peer("::ffff:93.184.216.34", 45000) == (GOOD, 45000)


def test_book_saves_and_reloads_and_skips_damaged_rows(tmp_path) -> None:
    path = tmp_path / "peers.json"
    book = share_peers.PeerBook(path)
    assert book.add(GOOD, 45000) and book.add("93.184.216.35", 45001)
    book.good(GOOD, 45000)
    book.save()
    again = share_peers.PeerBook(path)
    assert len(again) == 2 and again.sample() == [(GOOD, 45000)]
    now = time.time()
    path.write_text(json.dumps([[GOOD, 45000, now, now, 0], ["192.168.0.1", 45000, now, now, 0], [GOOD], "x", [GOOD, 45000, "a", 1, 0]]))
    assert len(share_peers.PeerBook(path)) == 1  # годна только первая запись
    path.write_text("not json")
    assert len(share_peers.PeerBook(path)) == 0
    path.write_text("[" + "0," * (share_peers.MAX_FILE_BYTES) + "0]")
    assert len(share_peers.PeerBook(path)) == 0  # слишком большой файл не читается


def test_peer_that_keeps_failing_is_dropped_and_a_success_forgives() -> None:
    book = share_peers.PeerBook()
    book.add(GOOD, 45000)
    book.bad(GOOD, 45000)
    book.bad(GOOD, 45000)
    book.good(GOOD, 45000)
    book.bad(GOOD, 45000)
    book.bad(GOOD, 45000)
    assert len(book) == 1
    book.bad(GOOD, 45000)
    assert len(book) == 0


def test_book_is_capped_and_pushes_out_the_worst_peer() -> None:
    book = share_peers.PeerBook()
    for index in range(share_peers.MAX_PEERS):
        book.add(f"93.184.{index // 250}.{index % 250 + 1}", 45000)
    victim = ("93.184.0.1", 45000)
    book.bad(*victim)
    assert len(book) == share_peers.MAX_PEERS
    book.add("8.8.8.8", 45000)
    assert len(book) == share_peers.MAX_PEERS and ("8.8.8.8", 45000) in book._items and victim not in book._items


def test_old_entries_are_forgotten_on_load(tmp_path) -> None:
    path = tmp_path / "peers.json"
    path.write_text(json.dumps([[GOOD, 45000, 100.0, 100.0, 0]]))
    assert len(share_peers.PeerBook(path, now=lambda: 100.0 + share_peers.FORGET_AFTER + 5)) == 0
    assert len(share_peers.PeerBook(path, now=lambda: 200.0)) == 1


def test_pick_prefers_peers_that_answered_and_respects_exclusions() -> None:
    book = share_peers.PeerBook(now=iter(range(1, 1000)).__next__)
    for last in range(1, 6):
        book.add(f"93.184.216.{last}", 45000)
    book.good("93.184.216.3", 45000)
    assert book.pick(1) == [("93.184.216.3", 45000)]
    assert ("93.184.216.3", 45000) not in book.pick(10, exclude={("93.184.216.3", 45000)})
    assert len(book.pick(3)) == 3


def test_sample_hides_unproven_and_struggling_peers() -> None:
    book = share_peers.PeerBook()
    for last in (1, 2, 3):
        book.add(f"93.184.216.{last}", 45000)
    book.good("93.184.216.1", 45000)
    book.good("93.184.216.2", 45000)
    book.bad("93.184.216.2", 45000)
    assert book.sample() == [("93.184.216.1", 45000)]


def test_parsing_a_neighbours_list_drops_everything_dishonest() -> None:
    raw = json.dumps({"peers": [[GOOD, 45000], [GOOD, 45000], ["192.168.0.2", 45000], ["x", 45000], [GOOD], "z", [GOOD, 80]]}).encode()
    assert share_peers.parse_peer_list(raw) == [(GOOD, 45000)]
    for bad in (b"", b"\xff\xfe", b"[]", b'{"peers": 5}', b'{"peers": "x"}', b"x" * (share_peers.MAX_FILE_BYTES + 1)):
        assert share_peers.parse_peer_list(bad) == []
    many = json.dumps({"peers": [[f"93.184.216.{i}", 45000] for i in range(1, 200)]}).encode()
    assert len(share_peers.parse_peer_list(many)) == share_peers.MAX_SHARED


def test_server_hands_out_its_known_peers_over_tls_and_not_without_a_provider(tmp_path) -> None:
    import ssl

    (tmp_path / "f.bin").write_bytes(b"data")
    files = share_serve.InstalledFiles(tmp_path, [])
    with_peers = share_serve.ShareServer(
        files, host="127.0.0.1", allow_loopback=True, ssl_context=share_cert.server_context(),
        peers_provider=lambda: [(GOOD, 45000)],
    )
    without = share_serve.ShareServer(files, host="127.0.0.1", allow_loopback=True)
    for server in (with_peers, without):
        server.start()
    try:
        connection = http.client.HTTPSConnection("127.0.0.1", with_peers.port, timeout=5, context=share_cert.client_context())
        connection.request("GET", share_peers.PEERS_ROUTE)
        reply = connection.getresponse()
        assert reply.status == 200 and share_peers.parse_peer_list(reply.read()) == [(GOOD, 45000)]
        connection.close()
        plain = http.client.HTTPConnection("127.0.0.1", without.port, timeout=5)
        plain.request("GET", share_peers.PEERS_ROUTE)
        assert plain.getresponse().status == 404  # у раздачи по локальной сети обмена адресами нет
        plain.close()
    finally:
        with_peers.stop()
        without.stop()
