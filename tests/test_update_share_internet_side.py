from __future__ import annotations

"""Раздача по интернету: TLS-сервер, проброс порта, запись на сервере; отдельно от раздачи по сети."""

import hashlib
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from zapret_update import share_client, share_discover, share_internet, share_peers, share_service
from zapret_update.manifest import FileEntry
from zapret_update.plan import UpdatePlan, Want
from zapret_update.share_portmap import Mapping
from zapret_update.share_serve import InstalledFiles

VERSION = "21.1.7.233"
WAN = "93.184.216.34"


class FakeMapper:
    instances: list = []

    def __init__(self, port, *, ok=True):
        self.port, self.ok, self.stopped = port, ok, False
        FakeMapper.instances.append(self)

    def start(self):
        return Mapping(WAN, self.port, "192.168.1.20", self.port) if self.ok else None

    def stop(self):
        self.stopped = True


def _files(tmp_path):
    data = os.urandom(64 * 1024)
    (tmp_path / "a.bin").write_bytes(data)
    entry = FileEntry("a.bin", len(data), hashlib.sha256(data).hexdigest(), len(data))
    return InstalledFiles(tmp_path, [entry]), entry, data


def _side(book=None, **kw):
    FakeMapper.instances = []
    registered = []
    side = share_internet.InternetSide(
        product="zapret-gui", book=book or share_peers.PeerBook(allow_local=True), host="127.0.0.1",
        mapper_factory=kw.pop("mapper_factory", FakeMapper),
        register_fn=lambda bases, product, version, port: registered.append((tuple(bases), product, version, port)) or True,
        bases=lambda: ["https://q.example/stats"], heartbeat=kw.pop("heartbeat", 3600), **kw,
    )
    return side, registered


def test_firewall_rule_is_removed_when_the_side_stops_or_never_got_a_port(tmp_path) -> None:
    files, _entry, _data = _files(tmp_path)
    closed: list[int] = []
    side, _ = _side(close_firewall=lambda: closed.append(1))
    assert side.start(files, VERSION) is True
    assert closed == []  # пока раздаёт — правило нужно
    side.stop()
    assert closed == [1]
    side.stop()  # повторная остановка ничего не ломает и не бьёт по брандмауэру
    assert closed == [1]

    refused, _ = _side(close_firewall=lambda: closed.append(2), mapper_factory=lambda port: FakeMapper(port, ok=False))
    assert refused.start(files, VERSION) is False
    assert closed == [1, 2]  # роутер не открыл порт: правило тоже убрано


def test_a_broken_firewall_cleanup_does_not_break_stopping(tmp_path) -> None:
    files, _entry, _data = _files(tmp_path)

    def boom():
        raise OSError("netsh missing")

    side, _ = _side(close_firewall=boom)
    assert side.start(files, VERSION) is True
    side.stop()
    assert not side.running


def test_started_side_serves_over_tls_registers_and_stops_cleanly(tmp_path) -> None:
    files, entry, data = _files(tmp_path)
    side, registered = _side()
    assert side.start(files, VERSION) is True and side.running
    try:
        for _ in range(50):
            if registered:
                break
            time.sleep(0.05)
        assert registered == [(("https://q.example/stats",), "zapret-gui", VERSION, side.port)]  # внешний порт = внутреннему
        neighbour = share_discover.Neighbour("127.0.0.1", side.port, tls=True)
        from zapret_update import share_prefill

        work = tmp_path / "work"
        result = share_prefill.prefill_from_neighbours(UpdatePlan(version="x", want=[Want(entry)]), work_dir=work, neighbours=[neighbour])
        assert result.files == 1 and (work / "ready" / entry.sha256).read_bytes() == data
        reply = share_internet.probe("127.0.0.1", side.port, allow_local=True)
        assert reply is not None and reply[1:] == ("zapret-gui", VERSION)
    finally:
        port = side.port
        side.stop()
    assert not side.running and FakeMapper.instances[0].stopped
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_heartbeat_registers_again(tmp_path) -> None:
    files, *_ = _files(tmp_path)
    side, registered = _side(heartbeat=0.05)
    side.start(files, VERSION)
    try:
        for _ in range(60):
            if len(registered) >= 3:
                break
            time.sleep(0.05)
        assert len(registered) >= 3
    finally:
        side.stop()
    count = len(registered)
    time.sleep(0.2)
    assert len(registered) == count  # после остановки больше не записывается


def test_router_that_does_not_open_the_port_means_no_internet_sharing_and_nothing_left_open(tmp_path) -> None:
    files, *_ = _files(tmp_path)
    reports: list[str] = []
    side, registered = _side(mapper_factory=lambda port: FakeMapper(port, ok=False), report=reports.append)
    assert side.start(files, VERSION) is False and not side.running and not registered
    assert any("Раздача по интернету недоступна" in text for text in reports)


def test_the_log_names_the_real_reason_the_router_gave(tmp_path) -> None:
    files, *_ = _files(tmp_path)
    reports: list[str] = []

    class Reasoned(FakeMapper):
        reason = "внешний адрес роутера не белый: общий адрес провайдера (CGNAT)"

    side, _ = _side(mapper_factory=lambda port: Reasoned(port, ok=False), report=reports.append)
    assert side.start(files, VERSION) is False
    assert any("CGNAT" in text for text in reports)


def test_firewall_gets_the_real_port_and_its_failure_is_not_fatal(tmp_path) -> None:
    files, *_ = _files(tmp_path)
    asked, reports = [], []
    side, _ = _side(open_firewall=lambda port: asked.append(port) or False, report=reports.append)
    assert side.start(files, VERSION) is True
    try:
        assert asked == [side.port] and any("брандмауэр" in text for text in reports)
    finally:
        side.stop()


def test_book_is_saved_on_stop(tmp_path) -> None:
    files, *_ = _files(tmp_path)
    book = share_peers.PeerBook(tmp_path / "peers.json", allow_local=True)
    book.add("127.0.0.1", 46111)
    side, _ = _side(book=book)
    side.start(files, VERSION)
    side.stop()
    assert (tmp_path / "peers.json").is_file()


# ── служба с двумя раздачами ───────────────────────────────────────────

def _service(tmp_path, *, lan=True, net=True, private=True, installed=True, internet=None):
    flags = {"lan": lan, "net": net}
    manifest = SimpleNamespace(files=(), version=VERSION)
    service = share_service.SharingService(
        root=tmp_path, enabled=lambda: flags["lan"], load_installed=lambda: manifest if installed else None,
        product="zapret-gui", current_version=VERSION, private_network=lambda: private, discovery_port=0,
        host="127.0.0.1", allow_loopback=True, report=lambda text: None,
        internet_enabled=lambda: flags["net"], internet=internet,
    )
    return service, flags


class FakeInternet:
    def __init__(self, ok=True):
        self.ok, self.running, self.starts, self.stops = ok, False, [], 0

    def start(self, files, version, manifest_provider=None, activity=None):
        self.provider, self.activity = manifest_provider, activity
        self.starts.append(version)
        self.running = self.ok
        return self.ok

    def stop(self):
        self.running = False
        self.stops += 1


def test_each_part_follows_its_own_switch(tmp_path) -> None:
    fake = FakeInternet()
    service, flags = _service(tmp_path, lan=True, net=False, internet=fake)
    try:
        assert service.refresh() is True and service.port and not fake.running
        flags["net"] = True
        assert service.refresh() is True and fake.running and fake.starts == [VERSION]
        flags["lan"] = False
        assert service.refresh() is True and service.port is None and fake.running  # осталась только раздача по интернету
        flags["net"] = False
        assert service.refresh() is False and not fake.running and fake.stops == 1
    finally:
        service.stop()


def test_internet_only_does_not_open_the_lan_side(tmp_path) -> None:
    fake = FakeInternet()
    service, _ = _service(tmp_path, lan=False, net=True, internet=fake)
    try:
        assert service.refresh() is True and service.port is None and service.discovery_port is None and fake.running
    finally:
        service.stop()


@pytest.mark.parametrize("case", ["public_network", "no_list"])
def test_internet_side_needs_the_same_conditions_as_the_lan_side(tmp_path, case) -> None:
    fake = FakeInternet()
    service, _ = _service(tmp_path, lan=False, net=True, internet=fake, private=case != "public_network", installed=case != "no_list")
    assert service.refresh() is False and not fake.running and not fake.starts


def test_internet_failure_does_not_touch_the_lan_side_and_does_not_escape(tmp_path) -> None:
    class Broken(FakeInternet):
        def start(self, files, version, manifest_provider=None, activity=None):
            raise RuntimeError("tls broke")

    service, _ = _service(tmp_path, lan=True, net=True, internet=Broken())
    try:
        assert service.refresh() is True and service.port  # раздача по сети жива
    finally:
        service.stop()


def test_internet_switch_without_an_internet_side_is_ignored(tmp_path) -> None:
    service, _ = _service(tmp_path, lan=False, net=True, internet=None)
    assert service.refresh() is False


def test_stop_closes_both_parts(tmp_path) -> None:
    fake = FakeInternet()
    service, _ = _service(tmp_path, internet=fake)
    service.refresh()
    service.stop()
    assert not service.running and not fake.running


# ── запасной путь: серверы не отдали файлы ─────────────────────────────

def _entry_and_plan(tmp_path, *, size=64 * 1024):
    data = os.urandom(size)
    entry = FileEntry("a.bin", len(data), hashlib.sha256(data).hexdigest(), len(data))
    return entry, data, UpdatePlan(version="x", want=[Want(entry)])


def test_internet_fallback_takes_files_from_internet_peers(tmp_path) -> None:
    from zapret_update import share_cert, share_serve

    entry, data, plan = _entry_and_plan(tmp_path)
    root = tmp_path / "peer"
    root.mkdir()
    (root / "a.bin").write_bytes(data)
    server = share_serve.ShareServer(
        share_serve.InstalledFiles(root, [entry]), host="127.0.0.1", allow_loopback=True, ssl_context=share_cert.server_context()
    )
    server.start()
    stages, reports = [], []
    try:
        result = share_client.fill_from_internet(
            plan, work_dir=tmp_path / "work", find=lambda: [share_discover.Neighbour("127.0.0.1", server.port, tls=True)],
            on_stage=stages.append, report=reports.append,
        )
    finally:
        server.stop()
    assert result.files == 1 and (tmp_path / "work" / "ready" / entry.sha256).read_bytes() == data
    assert any("в интернете" in text for text in stages) and "взято файлов 1" in reports[-1]


def test_internet_fallback_asks_even_for_files_the_server_would_have_sent_as_a_small_patch(tmp_path) -> None:
    from zapret_update import share_prefill
    from zapret_update.manifest import PatchEntry

    entry, _, _ = _entry_and_plan(tmp_path, size=2 * 1024 * 1024)
    plan = UpdatePlan(version="x", want=[Want(entry, PatchEntry("f" * 64, entry.sha256, 4096))])
    asked = []

    def fill(planned, **kwargs):
        asked.append(kwargs["everything"])
        return share_prefill.PrefillResult()

    share_client.fill_from_internet(plan, work_dir=tmp_path, find=lambda: [share_discover.Neighbour(WAN, 45000, tls=True)], fill=fill)
    assert asked == [True]
    assert share_prefill.worth_asking(plan.want[0]) is False  # обычным путём этот файл у соседей не просят


@pytest.mark.parametrize("case", ["nobody", "search_fails", "no_wants"])
def test_internet_fallback_never_raises_and_says_what_happened(tmp_path, case) -> None:
    entry, _, plan = _entry_and_plan(tmp_path)
    reports = []

    def boom():
        raise OSError("registry down")

    find = {"nobody": lambda: [], "search_fails": boom, "no_wants": lambda: [share_discover.Neighbour(WAN, 45000, tls=True)]}[case]
    if case == "no_wants":
        plan = UpdatePlan(version="x", want=[])
    result = share_client.fill_from_internet(plan, work_dir=tmp_path / "w", find=find, report=reports.append)
    assert result.files == 0
    assert (not reports) == (case == "no_wants")


def test_search_is_lan_only_and_the_switch_stops_the_question() -> None:
    lan = share_discover.Neighbour("192.168.1.9", 47000)
    assert share_client.Search("p", "1", find=lambda *_a: [lan]).result() == [lan]
    assert share_client.Search("p", "1", enabled=False, find=lambda *_a: [lan]).result() == []

    def boom(*_a):
        raise OSError("no")

    assert share_client.Search("p", "1", find=boom).result() == []


# ── правило брандмауэра для интернета ──────────────────────────────────

def test_internet_firewall_rule_names_the_port_and_allows_any_remote_but_only_private_profiles(monkeypatch) -> None:
    monkeypatch.setattr(share_service.sys, "platform", "win32")
    calls = []

    def run(cmd, **_kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=1 if "show" in cmd else 0, stdout="")

    exe = Path("C:/Program Files/Zapret/Zapret.exe")
    assert share_service.windows_open_firewall(
        exe, run=run, name=share_service.FIREWALL_RULE_NAME_INTERNET, remote="any", port=47821
    ) is True
    add = calls[-1]
    assert "remoteip=any" in add and "localport=47821" in add and "protocol=TCP" in add
    assert "profile=private,domain" in add and f"name={share_service.FIREWALL_RULE_NAME_INTERNET}" in add


def test_existing_internet_rule_is_kept_only_if_it_names_the_same_port(monkeypatch) -> None:
    monkeypatch.setattr(share_service.sys, "platform", "win32")
    exe = Path("C:/Zapret/Zapret.exe")
    text = f"Program: {exe}\nRemoteIP: Any\nLocalPort: 47821\n"
    calls = []

    def run(cmd, **_kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout=text)

    assert share_service.windows_open_firewall(exe, run=run, name="n", remote="any", port=47821) is True
    assert len(calls) == 1
    calls.clear()
    share_service.windows_open_firewall(exe, run=run, name="n", remote="any", port=47999)
    assert len(calls) == 3  # порт другой: правило заменено
