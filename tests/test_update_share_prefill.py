from __future__ import annotations

"""Файлы у соседей: берём только проверенное, обман и молчание не ломают обновление."""

import hashlib
import http.server
import os
import socket
import threading
from pathlib import Path

import pytest

from updater.share import client, discover, prefill, serve
from zapret_update import fetch
from zapret_update.manifest import FileEntry, PatchEntry
from zapret_update.plan import UpdatePlan, Want

BIG = 64 * 1024


def _entry(root: Path, name: str, data: bytes) -> FileEntry:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return FileEntry(name, len(data), hashlib.sha256(data).hexdigest(), zsize=len(data))


def _neighbour(root: Path, entries) -> tuple[serve.ShareServer, discover.Neighbour]:
    server = serve.ShareServer(serve.InstalledFiles(root, entries), host="127.0.0.1", allow_loopback=True)
    server.start()
    return server, discover.Neighbour("127.0.0.1", server.port)


class _Fake(http.server.ThreadingHTTPServer):
    """Нечестный или неисправный сосед: ``mode`` задаёт, как именно."""

    daemon_threads = True

    def __init__(self, mode: str) -> None:
        self.mode, self.requests = mode, 0
        super().__init__(("127.0.0.1", 0), _FakeHandler)
        self.release = threading.Event()
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.release.set()
        self.shutdown()
        self.server_close()

    @property
    def neighbour(self) -> discover.Neighbour:
        return discover.Neighbour("127.0.0.1", self.server_address[1])


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        server: _Fake = self.server  # type: ignore[assignment]
        server.requests += 1
        size = BIG
        if server.mode == "silent":
            server.release.wait(10)
            return
        if server.mode == "wrong-content":
            body = b"\x01" * BIG
        elif server.mode == "too-long":
            body = b"\x02" * (BIG + 100)
        elif server.mode == "lying-length":
            # Объявляет в десять раз больше, чем в списке, и молчит: без проверки
            # длины клиент дождался бы таймаута (обычный штраф), а не вычеркнул сразу.
            self.send_response(200)
            self.send_header("Content-Length", str(BIG * 10))
            self.end_headers()
            self.wfile.flush()
            server.release.wait(10)
            return
        else:
            body = b""
        self.send_response(200)
        self.send_header("Content-Length", str(len(body) if server.mode == "too-long" else size))
        self.end_headers()
        self.wfile.write(body)


def _plan(*entries: FileEntry, patches: dict | None = None) -> UpdatePlan:
    patches = patches or {}
    return UpdatePlan(version="9", want=[Want(entry, patches.get(entry.sha256)) for entry in entries])


@pytest.fixture
def world(tmp_path):
    neighbour_root, work = tmp_path / "neighbour", tmp_path / "work"
    servers = []

    def serve_files(*entries):
        server, who = _neighbour(neighbour_root, entries)
        servers.append(server)
        return who

    yield neighbour_root, work, serve_files, servers
    for server in servers:
        (server.stop if isinstance(server, serve.ShareServer) else server.close)()


def test_file_from_neighbour_lands_in_ready_with_right_content(world):
    root, work, serve_files, _ = world
    data = os.urandom(BIG * 3)
    entry = _entry(root, "a.bin", data)
    result = prefill.prefill_from_neighbours(_plan(entry), work_dir=work, neighbours=[serve_files(entry)])
    assert (result.files, result.bytes) == (1, len(data))
    assert (work / "ready" / entry.sha256).read_bytes() == data
    assert not list((work / "parts").glob("*"))


def test_neighbour_with_other_content_is_dropped_and_server_is_used(world):
    root, work, _, servers = world
    entry = _entry(root, "a.bin", os.urandom(BIG))
    second = _entry(root, "b.bin", os.urandom(BIG))
    liar = _Fake("wrong-content")
    servers.append(liar)
    result = prefill.prefill_from_neighbours(_plan(entry, second), work_dir=work, neighbours=[liar.neighbour], threads=1)
    assert result.files == 0
    assert not (work / "ready" / entry.sha256).exists()
    assert liar.requests == 1  # обман вычёркивает соседа сразу: второй файл у него не просили
    assert not list((work / "parts").glob("*"))  # недокачанный кусок не остаётся на диске


def test_neighbour_sending_more_than_listed_is_rejected(world):
    root, work, _, servers = world
    entry = _entry(root, "a.bin", os.urandom(BIG))
    liar = _Fake("too-long")
    servers.append(liar)
    assert prefill.prefill_from_neighbours(_plan(entry), work_dir=work, neighbours=[liar.neighbour]).files == 0
    assert not (work / "ready" / entry.sha256).exists()


def test_wrong_announced_length_drops_the_neighbour_at_once(world):
    root, work, _, servers = world
    entries = [_entry(root, f"{i}.bin", os.urandom(BIG)) for i in range(3)]
    liar = _Fake("lying-length")
    servers.append(liar)
    prefill.prefill_from_neighbours(_plan(*entries), work_dir=work, neighbours=[liar.neighbour], timeout=0.3, threads=1)
    assert liar.requests == 1


def test_silent_neighbour_is_skipped_after_two_strikes(world):
    root, work, _, servers = world
    entries = [_entry(root, f"{i}.bin", os.urandom(BIG)) for i in range(4)]
    silent = _Fake("silent")
    servers.append(silent)
    result = prefill.prefill_from_neighbours(
        _plan(*entries), work_dir=work, neighbours=[silent.neighbour], timeout=0.3, threads=1
    )
    assert result.files == 0
    assert silent.requests == prefill.STRIKES_TO_DROP


def test_missing_file_is_taken_from_the_next_neighbour(world):
    root, work, serve_files, _ = world
    entry = _entry(root, "a.bin", os.urandom(BIG))
    other_root = root.parent / "other"
    empty, _ = _neighbour(other_root, [])
    try:
        who_empty = discover.Neighbour("127.0.0.1", empty.port)
        result = prefill.prefill_from_neighbours(
            _plan(entry), work_dir=work, neighbours=[who_empty, serve_files(entry)]
        )
        assert result.files == 1
    finally:
        empty.stop()


def test_no_neighbours_does_nothing(world):
    _, work, *_ = world
    entry = FileEntry("a.bin", BIG, "0" * 64, BIG)
    assert prefill.prefill_from_neighbours(_plan(entry), work_dir=work, neighbours=[]).files == 0
    assert not work.exists()


def test_file_already_in_ready_is_not_asked_again(world):
    root, work, _, servers = world
    entry = _entry(root, "a.bin", os.urandom(BIG))
    (work / "ready").mkdir(parents=True)
    (work / "ready" / entry.sha256).write_bytes(b"already here")
    spy = _Fake("wrong-content")
    servers.append(spy)
    prefill.prefill_from_neighbours(_plan(entry), work_dir=work, neighbours=[spy.neighbour])
    assert spy.requests == 0


def test_tiny_files_and_files_with_a_much_smaller_patch_are_not_asked(world):
    root, work, _, servers = world
    tiny = _entry(root, "tiny.bin", b"x" * 100)
    big = _entry(root, "big.bin", os.urandom(BIG * 4))
    small_patch = PatchEntry("f" * 64, big.sha256, BIG // 8)  # заплатка в 32 раза меньше файла
    spy = _Fake("wrong-content")
    servers.append(spy)
    prefill.prefill_from_neighbours(
        _plan(tiny, big, patches={big.sha256: small_patch}), work_dir=work, neighbours=[spy.neighbour]
    )
    assert spy.requests == 0
    near_patch = PatchEntry("f" * 64, big.sha256, big.size // 2)  # заплатка почти с файл — сосед не хуже
    assert prefill.worth_asking(Want(big, near_patch)) and not prefill.worth_asking(Want(big, small_patch))
    assert not prefill.worth_asking(Want(tiny))


def test_cancel_stops_asking(world):
    root, work, _, servers = world
    entries = [_entry(root, f"{i}.bin", os.urandom(BIG)) for i in range(3)]
    spy = _Fake("wrong-content")
    servers.append(spy)
    result = prefill.prefill_from_neighbours(
        _plan(*entries), work_dir=work, neighbours=[spy.neighbour], cancelled=lambda: True
    )
    assert result.files == 0 and spy.requests == 0


def test_fetch_plan_uses_prefilled_files_without_touching_the_servers(world):
    root, work, serve_files, _ = world
    entry = _entry(root, "a.bin", os.urandom(BIG))
    plan = _plan(entry)
    prefill.prefill_from_neighbours(plan, work_dir=work, neighbours=[serve_files(entry)])
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()  # адрес, где никто не слушает: любой поход туда — ошибка
    sources = [fetch.Source("dead", lambda name: f"http://127.0.0.1:{port}/{name}")]
    ready = fetch.fetch_plan(plan, root=root, work_dir=work, sources=sources, sleep=lambda _s: None)
    assert ready[entry.sha256].read_bytes() == (root / "a.bin").read_bytes()


def test_client_never_raises_and_reports(tmp_path):
    def boom(*_a, **_k):
        raise RuntimeError("search broke")

    search = client.Search("zapret-gui", "1", find=boom)
    assert search.result() == []
    lines: list[str] = []
    entry = FileEntry("a.bin", BIG, "0" * 64, BIG)
    found = client.Search("zapret-gui", "1", find=lambda *_a: [discover.Neighbour("127.0.0.1", 47000)])
    out = client.fill_from_neighbours(
        _plan(entry), work_dir=tmp_path, search=found, report=lines.append,
        fill=lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")),
    )
    assert out.files == 0 and "не помогли" in lines[-1]


def test_client_disabled_does_not_search(tmp_path):
    called = []
    search = client.Search("zapret-gui", "1", enabled=False, find=lambda *_a: called.append(1) or [])
    assert search.result() == [] and not called


def test_search_returns_early_after_first_answer():
    responder = discover.Responder("zapret-gui", "1", 47950, host="127.0.0.1", port=0, allow_loopback=True)
    responder.start()
    try:
        import time

        began = time.monotonic()
        found = discover.find_neighbours(
            "zapret-gui", "1", timeout=5, targets=[("127.0.0.1", responder.port)], sources=["127.0.0.1"], allow_loopback=True
        )
        assert found and time.monotonic() - began < 2
    finally:
        responder.stop()
