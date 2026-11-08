from __future__ import annotations

"""Поиск соседей: отвечает только тот, у кого та же версия; мусор не слушаем."""

import json
import socket
import time

import pytest

from zapret_update import share_discover as discover

PRODUCT, VERSION = "zapret-gui", "21.1.7.231"


@pytest.fixture
def responder():
    started = []

    def make(version=VERSION, http_port=47900, **kwargs):
        item = discover.Responder(
            PRODUCT, version, http_port, host="127.0.0.1", port=0, allow_loopback=kwargs.pop("allow_loopback", True), **kwargs
        )
        item.start()
        started.append(item)
        return item

    yield make
    for item in started:
        item.stop()


def _find(item, *, product=PRODUCT, version=VERSION, timeout=0.6, allow_loopback=True, **kwargs):
    return discover.find_neighbours(
        product, version, timeout=timeout, targets=[("127.0.0.1", item.port)], sources=["127.0.0.1"],
        allow_loopback=allow_loopback, **kwargs,
    )


def test_same_version_answers_with_its_port(responder):
    item = responder(http_port=47900)
    assert _find(item) == [discover.Neighbour("127.0.0.1", 47900)]


def test_other_version_or_product_gets_no_answer(responder):
    item = responder()
    assert _find(item, version="21.1.7.230") == []
    assert _find(item, product="zapret-kvn") == []


def test_no_responder_gives_empty_list_after_the_timeout():
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    try:
        began = time.monotonic()
        found = discover.find_neighbours(
            PRODUCT, VERSION, timeout=0.3, targets=[("127.0.0.1", silent.getsockname()[1])], sources=["127.0.0.1"],
            allow_loopback=True,
        )
        assert found == [] and time.monotonic() - began < 2
    finally:
        silent.close()


@pytest.mark.parametrize(
    "packet",
    [
        b"",
        b"\xff\xfe\x00garbage",
        b"not json",
        b"[1,2,3]",
        b'{"z":"other","v":1,"op":"who","product":"zapret-gui","version":"21.1.7.231"}',
        b'{"z":"zapret-share","v":2,"op":"who","product":"zapret-gui","version":"21.1.7.231"}',
        b'{"z":"zapret-share","v":1,"op":"who"}',
        b"x" * 5000,
        json.dumps({"z": "zapret-share", "v": 1, "op": "who", "product": PRODUCT, "version": VERSION, "pad": "y" * 600}).encode(),
    ],
)
def test_garbage_is_ignored_and_responder_keeps_working(responder, packet):
    item = responder()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(0.3)
    try:
        probe.sendto(packet, ("127.0.0.1", item.port))
        with pytest.raises(TimeoutError):
            probe.recvfrom(1024)
    finally:
        probe.close()
    assert _find(item)  # после мусора всё ещё отвечает


def test_responder_ignores_addresses_outside_the_lan(responder):
    item = responder(allow_loopback=False)
    assert _find(item) == []


def test_answers_from_outside_the_lan_are_dropped(responder):
    item = responder()
    assert _find(item, allow_loopback=False) == []


@pytest.mark.parametrize("bad_port", [0, 80, 1023, 65536, -5, "47900", None, True, 4.5])
def test_nonsense_port_in_answer_is_dropped(bad_port):
    liar = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    liar.bind(("127.0.0.1", 0))
    liar.settimeout(2)
    answer = json.dumps({"z": "zapret-share", "v": 1, "op": "here", "product": PRODUCT, "version": VERSION, "port": bad_port})
    import threading

    def serve():
        try:
            _data, sender = liar.recvfrom(1024)
            liar.sendto(answer.encode(), sender)
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        found = discover.find_neighbours(
            PRODUCT, VERSION, timeout=0.5, targets=[("127.0.0.1", liar.getsockname()[1])], sources=["127.0.0.1"],
            allow_loopback=True,
        )
        assert found == []
    finally:
        thread.join(2)
        liar.close()


def test_answers_are_deduplicated_and_capped(responder):
    first, second = responder(http_port=47901), responder(http_port=47902)
    found = discover.find_neighbours(
        PRODUCT, VERSION, timeout=0.6, sources=["127.0.0.1", "127.0.0.1"],
        targets=[("127.0.0.1", first.port), ("127.0.0.1", second.port), ("127.0.0.1", first.port)],
        allow_loopback=True,
    )
    assert sorted(item.port for item in found) == [47901, 47902]
    capped = discover.find_neighbours(
        PRODUCT, VERSION, timeout=0.6, sources=["127.0.0.1"],
        targets=[("127.0.0.1", first.port), ("127.0.0.1", second.port)], allow_loopback=True, limit=1,
    )
    assert len(capped) == 1


def test_responder_limits_replies_per_second(responder):
    item = responder()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(0.3)
    query = json.dumps({"z": "zapret-share", "v": 1, "op": "who", "product": PRODUCT, "version": VERSION}).encode()
    try:
        for _ in range(discover.REPLIES_PER_SECOND * 3):
            probe.sendto(query, ("127.0.0.1", item.port))
        time.sleep(0.2)
        replies = 0
        while True:
            try:
                probe.recvfrom(1024)
                replies += 1
            except TimeoutError:
                break
        assert 0 < replies <= discover.REPLIES_PER_SECOND + 1
    finally:
        probe.close()


def test_local_addresses_are_only_lan_ones():
    from zapret_update.share_serve import is_lan_address

    assert all(is_lan_address(address) for address in discover.local_lan_addresses())


def test_valid_packet_one_byte_over_the_limit_is_ignored(responder):
    item = responder()
    base = {"z": "zapret-share", "v": 1, "op": "who", "product": PRODUCT, "version": VERSION, "pad": ""}
    size = len(json.dumps(base, separators=(",", ":")))
    base["pad"] = "p" * (discover.MAX_PACKET + 1 - size)
    packet = json.dumps(base, separators=(",", ":")).encode()
    assert len(packet) == discover.MAX_PACKET + 1
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(0.3)
    try:
        probe.sendto(packet, ("127.0.0.1", item.port))
        with pytest.raises(TimeoutError):
            probe.recvfrom(1024)
    finally:
        probe.close()
