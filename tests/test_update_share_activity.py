from __future__ import annotations

"""Журнал раздачи: кто к нам обращался и к кому ходили мы (только в памяти, без записи на диск)."""

import hashlib
import http.client
import os
import socket
import time
from types import SimpleNamespace

import pytest

from zapret_update import share_activity, share_cert, share_discover, share_prefill, share_serve, share_service
from zapret_update.manifest import FileEntry
from zapret_update.plan import UpdatePlan, Want

BIG = 64 * 1024


def test_log_keeps_newest_first_and_a_fixed_number_of_events() -> None:
    log = share_activity.ActivityLog(3, now=iter(range(1, 100)).__next__)
    for index in range(5):
        log.add("in", "lan", f"10.0.0.{index}", "file", f"f{index}", index)
    events = log.snapshot()
    assert [e.peer for e in events] == ["10.0.0.4", "10.0.0.3", "10.0.0.2"] and events[0].when > events[1].when
    log.clear()
    assert log.snapshot() == []


def test_long_text_is_cut_and_sizes_are_never_negative() -> None:
    log = share_activity.ActivityLog()
    log.add("in", "lan", "x" * 500, "file", "y" * 900, -5)
    event = log.snapshot()[0]
    assert len(event.peer) <= 64 and len(event.detail) <= 200 and event.size == 0


def test_summary_counts_visitors_bytes_and_internet_events() -> None:
    log = share_activity.ActivityLog()
    log.add("in", "lan", "10.0.0.1", "search")  # вопрос в сети — не гость
    log.add("in", "lan", "10.0.0.2", "file", "a", 100)
    log.add("in", "internet", "93.184.216.34", "file", "b", 200)
    log.add("in", "internet", "93.184.216.34", "peers", "", 30)
    log.add("in", "lan", "10.0.0.3", "file", "c", 999, "not_found")  # не отдано: в байты не идёт
    log.add("out", "lan", "10.0.0.9", "file", "d", 50)
    log.add("out", "lan", "10.0.0.9", "file", "e", 70, "failed")
    assert log.summary() == {"events": 7, "visitors": 3, "given_bytes": 330, "taken_bytes": 50, "internet_events": 2}


def _server(tmp_path, *, tls=False, via="lan", **kw):
    data = os.urandom(BIG)
    (tmp_path / "a.bin").write_bytes(data)
    entry = FileEntry("_internal/a.bin", len(data), hashlib.sha256(data).hexdigest(), len(data))
    (tmp_path / "_internal").mkdir()
    (tmp_path / "_internal" / "a.bin").write_bytes(data)
    log = share_activity.ActivityLog()
    server = share_serve.ShareServer(
        share_serve.InstalledFiles(tmp_path, [entry]), host="127.0.0.1", allow_loopback=True, activity=log, via=via,
        ssl_context=share_cert.server_context() if tls else None, **kw,
    )
    server.start()
    return server, log, entry


def _get(server, target, *, tls=False):
    connection = (
        http.client.HTTPSConnection("127.0.0.1", server.port, timeout=5, context=share_cert.client_context())
        if tls else http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    )
    connection.request("GET", target)
    reply = connection.getresponse()
    body = reply.read()
    connection.close()
    return reply.status, body


def test_file_request_is_recorded_with_the_name_from_the_signed_list_and_the_size(tmp_path) -> None:
    server, log, entry = _server(tmp_path)
    try:
        assert _get(server, share_serve.ROUTE + entry.sha256)[0] == 200
        for _ in range(40):
            if log.snapshot():
                break
            time.sleep(0.05)
    finally:
        server.stop()
    event = log.snapshot()[0]
    assert (event.direction, event.via, event.peer, event.what) == ("in", "lan", "127.0.0.1", "file")
    assert (event.detail, event.size, event.result) == ("_internal/a.bin", BIG, "ok")


def test_refused_missing_and_foreign_requests_are_recorded_too(tmp_path) -> None:
    data = os.urandom(10)
    (tmp_path / "x.bin").write_bytes(data)
    log = share_activity.ActivityLog()
    strict = share_serve.ShareServer(  # loopback не разрешён: чужой адрес
        share_serve.InstalledFiles(tmp_path, []), host="127.0.0.1", activity=log
    )
    strict.start()
    try:
        assert _get(strict, "/anything")[0] == 403
    finally:
        strict.stop()
    open_server, log2, _ = _server(tmp_path / "o") if (tmp_path / "o").mkdir() is None else None
    try:
        assert _get(open_server, share_serve.ROUTE + "f" * 64)[0] == 404
        assert _get(open_server, "/nonsense")[0] == 404
        for _ in range(40):
            if len(log2.snapshot()) >= 2:
                break
            time.sleep(0.05)
    finally:
        open_server.stop()
    assert [(e.what, e.result) for e in log.snapshot()] == [("request", "refused")]
    assert sorted((e.what, e.result) for e in log2.snapshot()) == [("file", "not_found"), ("request", "not_found")]


def test_internet_server_labels_its_events_and_records_peer_exchange_and_the_signed_list(tmp_path) -> None:
    server, log, _ = _server(
        tmp_path, tls=True, via="internet", peers_provider=lambda: [("93.184.216.34", 45000)], identity=("p", "1"),
        manifest_provider=lambda: (b"LIST", b"SIG"),
    )
    try:
        assert _get(server, share_peers_route(), tls=True)[0] == 200
        assert _get(server, share_manifest_route(), tls=True)[0] == 200
        for _ in range(40):
            if len(log.snapshot()) >= 2:
                break
            time.sleep(0.05)
    finally:
        server.stop()
    assert sorted((e.via, e.what, e.result) for e in log.snapshot()) == [("internet", "manifest", "ok"), ("internet", "peers", "ok")]
    assert all(e.size > 0 for e in log.snapshot())


def share_peers_route():
    from zapret_update import share_peers

    return share_peers.PEERS_ROUTE


def share_manifest_route():
    from zapret_update import share_manifest

    return share_manifest.MANIFEST_ROUTE


def test_a_receiver_that_leaves_halfway_is_recorded_as_interrupted(tmp_path) -> None:
    data = os.urandom(48 * 1024 * 1024)
    (tmp_path / "big.bin").write_bytes(data)
    entry = FileEntry("big.bin", len(data), hashlib.sha256(data).hexdigest(), len(data))
    log = share_activity.ActivityLog()
    server = share_serve.ShareServer(
        share_serve.InstalledFiles(tmp_path, [entry]), host="127.0.0.1", allow_loopback=True, activity=log
    )
    server.start()
    try:
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(f"GET {share_serve.ROUTE}{entry.sha256} HTTP/1.0\r\n\r\n".encode())
        sock.recv(4096)
        sock.close()  # ушёл, не дочитав
        for _ in range(100):
            if log.snapshot():
                break
            time.sleep(0.05)
    finally:
        server.stop()
    event = log.snapshot()[0]
    assert event.result == "interrupted" and 0 < event.size < len(data)


def test_responder_records_who_asked_in_the_network() -> None:
    log = share_activity.ActivityLog()
    responder = share_discover.Responder("p", "1", 47000, host="127.0.0.1", port=0, allow_loopback=True, activity=log)
    responder.start()
    try:
        found = share_discover.find_neighbours(
            "p", "1", timeout=1, targets=[("127.0.0.1", responder.port)], sources=["127.0.0.1"], allow_loopback=True
        )
        assert found
    finally:
        responder.stop()
    event = log.snapshot()[0]
    assert (event.direction, event.via, event.what, event.peer) == ("in", "lan", "search", "127.0.0.1")


def test_our_own_downloads_are_recorded_as_outgoing(tmp_path) -> None:
    server, _, entry = _server(tmp_path / "n") if (tmp_path / "n").mkdir() is None else None
    log = share_activity.ActivityLog()
    try:
        neighbour = share_discover.Neighbour("127.0.0.1", server.port)
        missing = FileEntry("_internal/missing.bin", 200_000, "e" * 64, 200_000)
        share_prefill.prefill_from_neighbours(
            UpdatePlan(version="x", want=[Want(entry), Want(missing)]), work_dir=tmp_path / "w", neighbours=[neighbour],
            activity=log, threads=1,
        )
    finally:
        server.stop()
    seen = {(e.detail, e.result): e for e in log.snapshot()}
    assert seen[("_internal/a.bin", "ok")].size == BIG and seen[("_internal/a.bin", "ok")].direction == "out"
    assert ("_internal/missing.bin", "not_found") in seen
    assert {e.via for e in log.snapshot()} == {"lan"}


def test_service_gives_one_log_to_its_servers_and_responder(tmp_path) -> None:
    seen = {}

    class Net:
        running = False

        def start(self, files, version, manifest_provider=None, activity=None):
            seen["activity"] = activity
            self.running = True
            return True

        def stop(self):
            self.running = False

    service = share_service.SharingService(
        root=tmp_path, enabled=lambda: True, load_installed=lambda: SimpleNamespace(files=(), version="1"),
        product="p", current_version="1", discovery_port=0, host="127.0.0.1", allow_loopback=True,
        internet_enabled=lambda: True, internet=Net(),
    )
    try:
        assert service.refresh() is True
        assert seen["activity"] is service.activity
        assert service._server.activity is service.activity and service._server.via == "lan"
        assert service._responder._activity is service.activity
    finally:
        service.stop()
