from __future__ import annotations

"""Когда программа раздаёт обновления и как открывает для этого брандмауэр."""

import hashlib
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from zapret_update import share_discover as discover
from zapret_update import share_prefill as prefill
from zapret_update import share_service as runtime
from zapret_update.manifest import FileEntry
from zapret_update.plan import UpdatePlan, Want

BIG = 64 * 1024


def _installed(root: Path):
    data = os.urandom(BIG)
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "app.bin").write_bytes(data)
    entry = FileEntry("_internal/app.bin", len(data), hashlib.sha256(data).hexdigest(), len(data))
    return SimpleNamespace(files=(entry,), version="21.1.7.231"), entry, data


def _service(tmp_path, installed, *, enabled=lambda: True, private=lambda: True, firewall=None, reports=None):
    return runtime.SharingService(
        root=tmp_path, enabled=enabled, load_installed=lambda: installed, product="zapret-gui",
        exe_path=tmp_path / "_internal" / "Zapret.exe", private_network=private, open_firewall=firewall,
        discovery_port=0, host="127.0.0.1", allow_loopback=True,
        report=(reports.append if reports is not None else (lambda _t: None)),
    )


def test_running_service_is_found_and_serves_a_verified_file(tmp_path):
    installed, entry, data = _installed(tmp_path / "neighbour")
    service = _service(tmp_path / "neighbour", installed)
    assert service.refresh() is True
    try:
        found = discover.find_neighbours(
            "zapret-gui", "21.1.7.231", timeout=1, targets=[("127.0.0.1", service.discovery_port)],
            sources=["127.0.0.1"], allow_loopback=True,
        )
        assert found == [discover.Neighbour("127.0.0.1", service.port)]
        work = tmp_path / "work"
        result = prefill.prefill_from_neighbours(
            UpdatePlan(version="x", want=[Want(entry)]), work_dir=work, neighbours=found
        )
        assert result.files == 1 and (work / "ready" / entry.sha256).read_bytes() == data
    finally:
        service.stop()


def test_switch_off_closes_the_ports_and_switch_on_opens_again(tmp_path):
    installed, *_ = _installed(tmp_path)
    flag = {"on": True}
    service = _service(tmp_path, installed, enabled=lambda: flag["on"])
    assert service.refresh() and service.running
    port = service.port
    flag["on"] = False
    assert service.refresh() is False and not service.running
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    flag["on"] = True
    assert service.refresh() is True
    service.stop()


@pytest.mark.parametrize("case", ["disabled", "no_list", "public_network", "list_broken"])
def test_service_stays_off_and_says_why(tmp_path, case):
    installed, *_ = _installed(tmp_path)
    reports: list[str] = []
    kwargs = {"disabled": {"enabled": lambda: False}, "public_network": {"private": lambda: False}}.get(case, {})
    service = _service(tmp_path, None if case == "no_list" else installed, reports=reports, **kwargs)
    if case == "list_broken":
        service._load_installed = lambda: (_ for _ in ()).throw(ValueError("bad signature"))
    assert service.refresh() is False and not service.running
    assert (not reports) == (case == "disabled")
    expected = {"no_list": "нет подписанного списка", "public_network": "сеть не частная"}.get(case)
    if expected:
        assert expected in reports[-1]


def test_firewall_failure_does_not_stop_sharing(tmp_path):
    installed, *_ = _installed(tmp_path)
    asked: list[Path] = []
    reports: list[str] = []
    service = _service(tmp_path, installed, firewall=lambda exe: asked.append(exe) or False, reports=reports)
    try:
        assert service.refresh() is True
        assert asked == [tmp_path / "_internal" / "Zapret.exe"] and any("брандмауэр" in text for text in reports)
    finally:
        service.stop()


def test_busy_discovery_port_is_reported_and_leaves_nothing_open(tmp_path):
    installed, *_ = _installed(tmp_path)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    # Без SO_REUSEADDR на Linux второй bind на этот порт упадёт — как у чужой программы.
    service = _service(tmp_path, installed)
    service._discovery_port = blocker.getsockname()[1]
    try:
        assert service.refresh() is False and not service.running
    finally:
        blocker.close()


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["Private:Internet"], True),
        (["DomainAuthenticated:LocalNetwork"], True),
        (["Public:Internet"], False),
        (["Public:Internet", "Private:Internet"], True),
        (["Private:Disconnected"], False),
        (["Private"], False),
        ([], False),
        (["garbage"], False),
    ],
)
def test_lan_allowed_lines(lines, expected):
    assert runtime.lan_allowed(lines) is expected


def _fake_run(responses):
    calls: list[list[str]] = []

    def run(cmd, **_kw):
        calls.append(list(cmd))
        return responses.pop(0)

    return run, calls


def test_firewall_rule_is_added_for_local_subnet_and_private_networks(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    run, calls = _fake_run([SimpleNamespace(returncode=1, stdout=""), SimpleNamespace(returncode=0, stdout=""), SimpleNamespace(returncode=0, stdout="")])
    exe = Path("C:/Zapret/_internal/Zapret.exe")
    assert runtime.windows_open_firewall(exe, run=run) is True
    add = calls[-1]
    assert "add" in add and "dir=in" in add and "action=allow" in add
    assert "remoteip=localsubnet" in add and "profile=private,domain" in add and f"program={exe}" in add


def test_existing_correct_firewall_rule_is_left_alone(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    exe = Path("C:/Zapret/_internal/Zapret.exe")
    shown = SimpleNamespace(returncode=0, stdout=f"Program: {exe}\nRemoteIP: LocalSubnet\n")
    run, calls = _fake_run([shown])
    assert runtime.windows_open_firewall(exe, run=run) is True
    assert len(calls) == 1


def test_firewall_without_rights_reports_false(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    run, _ = _fake_run([SimpleNamespace(returncode=1, stdout=""), SimpleNamespace(returncode=1, stdout=""), SimpleNamespace(returncode=1, stdout="")])
    assert runtime.windows_open_firewall(Path("C:/x.exe"), run=run) is False


def test_windows_network_check_parses_powershell_and_fails_closed(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    ok, _ = _fake_run([SimpleNamespace(returncode=0, stdout="Public:Internet\r\nPrivate:Internet\r\n")])
    assert runtime.windows_private_network(run=ok) is True
    only_public, _ = _fake_run([SimpleNamespace(returncode=0, stdout="Public:Internet\r\n")])
    assert runtime.windows_private_network(run=only_public) is False
    failed, _ = _fake_run([SimpleNamespace(returncode=1, stdout="Private:Internet")])
    assert runtime.windows_private_network(run=failed) is False

    def boom(*_a, **_k):
        raise OSError("no powershell")

    assert runtime.windows_private_network(run=boom) is False


def test_not_windows_never_shares_by_default():
    assert runtime.windows_private_network() is False or os.name == "nt"
    assert runtime.windows_open_firewall(Path("x")) is False or os.name == "nt"


def test_flipping_the_switch_applies_at_once_and_a_failure_does_not_lose_the_setting(monkeypatch):
    from unittest.mock import Mock

    from settings import store
    from updater import commands
    from updater.share import wiring

    saved, refreshed = Mock(), Mock()
    monkeypatch.setattr(store, "set_share_updates_lan", saved)
    monkeypatch.setattr(wiring, "refresh_default", refreshed)
    commands.set_share_updates_lan(False)
    saved.assert_called_once_with(False)
    refreshed.assert_called_once_with()

    refreshed.side_effect = RuntimeError("no network stack")
    commands.set_share_updates_lan(True)  # сбой раздачи не должен терять сохранённую настройку
    assert saved.call_args.args == (True,)


def test_windows_launcher_never_uses_the_detached_flag_that_silences_powershell(monkeypatch):
    """PowerShell с DETACHED_PROCESS ничего не пишет в перехваченный канал (найдено на win10)."""
    import subprocess

    seen = {}

    class Info:
        dwFlags = 0
        wShowWindow = 1

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "STARTUPINFO", Info, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.update(kw) or SimpleNamespace(returncode=0, stdout="ok"))
    runtime._run_windows(["x"], timeout=3)
    flags = seen["creationflags"]
    assert flags & 0x08000000 and not flags & 0x00000008  # без окна, но не отсоединён
    assert seen["capture_output"] and seen["stdin"] == subprocess.DEVNULL and seen["startupinfo"].wShowWindow == 0
