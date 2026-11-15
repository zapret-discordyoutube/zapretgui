"""The root helper: argument policy, the start/stop lifecycle with its safety order, the socket and the client.
Every system call is a fake: no root, no nftables, no engine."""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from platforms.linux import capture_rules
from platforms.linux.helper import client as helper_client
from platforms.linux.helper import policy, protocol, server
from platforms.linux.helper.service import Caller, HelperService, SystemCalls

# -- policy ----------------------------------------------------------------------------------------


def test_refused_options_are_those_the_helper_sets_itself(tmp_path) -> None:
    for option in ("--qnum=1", "--daemon", "--pidfile=/x", "--user=root", "--uid=0", "--debug=@/etc/x", "--dry-run", "--writable=/"):
        assert policy.check_args([option], [tmp_path]), option


def test_only_options_are_accepted(tmp_path) -> None:
    assert policy.check_args(["ls"], [tmp_path])
    assert policy.check_args(["--ok", "bad\nline"], [tmp_path])
    assert policy.check_args(["--a=b\0c"], [tmp_path])


def test_files_must_be_absolute_and_inside_a_root(tmp_path) -> None:
    root = tmp_path / "opt"
    root.mkdir()
    (root / "lua").mkdir()
    assert policy.check_args([f"--lua-init=@{root}/lua/x.lua", f"--blob=tls:@{root}/bin/t.bin", f"--hostlist={root}/lists/a.txt"], [root]) == []
    assert any("absolute" in p for p in policy.check_args(["--lua-init=@lua/x.lua"], [root]))
    assert any("outside" in p for p in policy.check_args(["--lua-init=@/etc/shadow"], [root]))
    assert any("outside" in p for p in policy.check_args(["--hostlist=/etc/passwd"], [root]))
    assert any("outside" in p for p in policy.check_args([f"--lua-init=@{root}/../etc/x"], [root]))


def test_a_symlink_cannot_lead_out_of_the_allowed_folder(tmp_path) -> None:
    root = tmp_path / "opt"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    (root / "link").symlink_to(outside)
    assert any("outside" in p for p in policy.check_args([f"--hostlist={root}/link"], [root]))


def test_capture_options_and_plain_values_are_not_treated_as_files(tmp_path) -> None:
    assert policy.check_args(["--wf-raw-part=@windivert.filter/x.txt", "--wf-tcp-out=443", "--filter-tcp=443", "--payload=tls_client_hello", "--lua-desync=fake:blob=tls1"], [tmp_path]) == []


def test_the_callers_data_folder_is_allowed_but_not_the_rest_of_the_home(tmp_path) -> None:
    roots = policy.allowed_roots_for(str(tmp_path / "home" / "ann"), tmp_path / "opt")
    assert policy.check_args([f"--hostlist={tmp_path}/home/ann/.local/share/zapretgui/lists/a.txt"], roots) == []
    assert policy.check_args([f"--hostlist={tmp_path}/home/ann/.ssh/id_rsa"], roots)


# -- protocol ---------------------------------------------------------------------------------------


def test_messages_round_trip_over_a_socket_pair() -> None:
    a, b = socket.socketpair()
    a.sendall(protocol.encode({"op": "status", "text": "привет"}))
    assert protocol.read_message(b) == {"op": "status", "text": "привет"}
    a.close()
    assert protocol.read_message(b) is None


@pytest.mark.parametrize("raw", [b"not json\n", b"[1, 2]\n", b"\xff\xfe\n"])
def test_bad_messages_are_protocol_errors(raw) -> None:
    a, b = socket.socketpair()
    a.sendall(raw)
    with pytest.raises(protocol.ProtocolError):
        protocol.read_message(b)


def test_a_huge_message_is_refused() -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.encode({"x": "y" * (protocol.MAX_MESSAGE_BYTES + 1)})


# -- the service -----------------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, pid: int = 4242, exit_code: int | None = None) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.events: list[str] = []

    def poll(self):
        return self.exit_code

    def terminate(self) -> None:
        self.events.append("terminate")
        self.exit_code = 0

    def kill(self) -> None:
        self.events.append("kill")
        self.exit_code = -9

    def wait(self, timeout=None) -> int:
        return self.exit_code or 0


class Recorder:
    """Records the order of everything the service does to the system."""

    def __init__(self, process: FakeProcess | None = None, nft_rc: int = 0) -> None:
        self.events: list[str] = []
        self.process = process or FakeProcess()
        self.nft_rc = nft_rc
        self.sysctls: dict[str, str] = {"net.netfilter.nf_conntrack_tcp_be_liberal": "0"}
        self.alive_pids: set[int] = set()
        self.engine_pids: set[int] = set()
        self.killed: list[tuple[int, int]] = []
        self.spawned: list[list[str]] = []

    def calls(self) -> SystemCalls:
        def nft(script: str):
            kind = "nft-remove" if script == capture_rules.teardown_ruleset() else "nft-install"
            self.events.append(kind)
            return (0, "") if kind == "nft-remove" else (self.nft_rc, "Error: boom" if self.nft_rc else "")

        def spawn(command):
            self.events.append("spawn")
            self.spawned.append(command)
            original = self.process.terminate
            self.process.terminate = lambda: (self.events.append("engine-terminate"), original())[1]  # type: ignore[method-assign]
            return self.process

        def write_sysctl(key, value):
            self.events.append(f"sysctl {key}={value}")
            self.sysctls[key] = value
            return True

        return SystemCalls(
            nft=nft,
            read_sysctl=lambda key: self.sysctls.get(key),
            write_sysctl=write_sysctl,
            spawn=spawn,
            pid_is_engine=lambda pid, _path: pid in self.engine_pids,
            kill_pid=lambda pid, sig: self.killed.append((pid, sig)),
            pid_alive=lambda pid: pid in self.alive_pids,
            sleep=lambda _s: None,
        )


GOOD_ARGS = ["--wf-tcp-out=80,443", "--wf-udp-out=443", "--filter-tcp=443", "--payload=tls_client_hello", "--lua-desync=fake:blob=tls1"]


@pytest.fixture()
def world(tmp_path):
    program = tmp_path / "opt"
    (program / "bin").mkdir(parents=True)
    engine = program / "bin" / "nfqws2"
    engine.write_text("#!/bin/sh\n")
    engine.chmod(0o755)
    recorder = Recorder()

    def make(**kwargs):
        return HelperService(
            recorder.calls(),
            state_path=str(tmp_path / "state.json"),
            engine_path=engine,
            program_root=program,
            log_path=str(tmp_path / "engine.log"),
            **kwargs,
        )

    return SimpleWorld(make(), make, recorder, tmp_path, program)


class SimpleWorld:
    def __init__(self, service, make, recorder, tmp_path, program) -> None:
        self.service, self.make, self.recorder, self.tmp_path, self.program = service, make, recorder, tmp_path, program
        self.user = Caller(uid=1000, home=str(tmp_path / "home"), gid=1001)


def test_start_spawns_the_engine_first_and_installs_the_rules_second(world) -> None:
    answer = world.service.start(GOOD_ARGS, world.user, label="Preset")
    assert answer["ok"] and answer["pid"] == 4242
    events = world.recorder.events
    assert events.index("spawn") < events.index("nft-install")
    command = world.recorder.spawned[0]
    assert command[0] == str(world.program / "bin" / "nfqws2")
    assert "--qnum=200" in command and "--uid=1000:1001" in command  # the engine continues as the caller
    assert not any(arg.startswith("--wf-") for arg in command)  # the capture options never reach the engine
    assert "--filter-tcp=443" in command and "--lua-desync=fake:blob=tls1" in command
    assert world.recorder.sysctls["net.netfilter.nf_conntrack_tcp_be_liberal"] == "1"
    assert json.loads((world.tmp_path / "state.json").read_text())["pid"] == 4242
    assert world.service.status()["running"] is True


def test_the_rules_are_removed_before_the_engine_is_stopped(world) -> None:
    world.service.start(GOOD_ARGS, world.user)
    world.recorder.events.clear()
    answer = world.service.stop()
    assert answer == {"ok": True, "was_running": True}
    events = world.recorder.events
    assert events.index("nft-remove") < events.index("engine-terminate")
    assert world.recorder.sysctls["net.netfilter.nf_conntrack_tcp_be_liberal"] == "0"  # restored
    assert not (world.tmp_path / "state.json").exists()
    assert world.service.status()["running"] is False


def test_stop_when_nothing_runs_is_fine_and_still_removes_the_table(world) -> None:
    assert world.service.stop() == {"ok": True, "was_running": False}
    assert "nft-remove" in world.recorder.events


def test_options_that_cannot_be_translated_refuse_the_start_and_change_nothing(world) -> None:
    answer = world.service.start(["--wf-raw-part=@windivert.filter/missing.txt", "--wf-tcp-out=443"], world.user)
    assert not answer["ok"] and "not supported on Linux" in answer["error"]
    assert world.recorder.events == [] and world.recorder.spawned == []


def test_a_preset_that_captures_nothing_is_refused(world) -> None:
    answer = world.service.start(["--filter-tcp=443"], world.user)
    assert not answer["ok"] and "captures no traffic" in answer["error"]
    assert world.recorder.events == []


def test_policy_violations_refuse_the_start_and_change_nothing(world) -> None:
    answer = world.service.start(GOOD_ARGS + ["--lua-init=@/etc/shadow"], world.user)
    assert not answer["ok"] and "outside" in answer["error"]
    answer = world.service.start(GOOD_ARGS + ["--user=root"], world.user)
    assert not answer["ok"] and "managed by the helper" in answer["error"]
    assert world.recorder.events == []


def test_garbage_arguments_are_answered_not_raised(world) -> None:
    assert not world.service.start("--wf-tcp-out=1", world.user)["ok"]
    assert not world.service.start([1, 2], world.user)["ok"]
    assert not world.service.handle({"op": "format-disk"}, world.user)["ok"]
    assert not world.service.handle({"op": "start"}, world.user)["ok"]


def test_an_unexpected_error_is_answered_and_its_traceback_goes_to_the_journal(world, monkeypatch, capsys) -> None:
    def broken(*_args, **_kwargs):
        raise UnicodeEncodeError("ascii", "·", 0, 1, "ordinal not in range(128)")

    monkeypatch.setattr(world.service, "start", broken)
    answer = world.service.handle({"op": "start", "args": ["--wf-tcp-out=443"]}, world.user)
    assert answer["ok"] is False and "UnicodeEncodeError" in answer["error"]
    assert "Traceback" in capsys.readouterr().err


def test_an_engine_that_dies_at_once_leaves_no_rules_and_restores_the_sysctls(world) -> None:
    world.recorder.process.exit_code = 3
    answer = world.service.start(GOOD_ARGS, world.user)
    assert not answer["ok"] and "exited at once" in answer["error"]
    assert "nft-install" not in world.recorder.events
    assert world.recorder.sysctls["net.netfilter.nf_conntrack_tcp_be_liberal"] == "0"
    assert world.service.status()["running"] is False


def test_failing_rules_stop_the_engine_again(world) -> None:
    world.recorder.nft_rc = 1
    answer = world.service.start(GOOD_ARGS, world.user)
    assert not answer["ok"] and "firewall rules" in answer["error"]
    assert "engine-terminate" in world.recorder.events
    assert world.recorder.sysctls["net.netfilter.nf_conntrack_tcp_be_liberal"] == "0"
    assert world.service.status()["running"] is False


def test_a_missing_engine_is_a_clear_error(world) -> None:
    (world.program / "bin" / "nfqws2").unlink()
    answer = world.service.start(GOOD_ARGS, world.user)
    assert not answer["ok"] and "not installed" in answer["error"]
    assert world.recorder.events == []


def test_starting_again_replaces_the_previous_run_and_never_stacks(world) -> None:
    world.service.start(GOOD_ARGS, world.user)
    world.recorder.events.clear()
    world.service.start(GOOD_ARGS, world.user)
    events = world.recorder.events
    assert events.index("engine-terminate") < events.index("spawn")
    assert events.count("spawn") == 1


def test_the_rules_go_at_once_when_the_engine_dies_on_its_own(world) -> None:
    world.service.start(GOOD_ARGS, world.user)
    world.recorder.events.clear()
    assert world.service.check_engine_alive() is True
    world.recorder.process.exit_code = 1  # the engine crashed
    assert world.service.check_engine_alive() is False
    assert "nft-remove" in world.recorder.events
    status = world.service.status()
    assert status["running"] is False


def test_status_reports_an_engine_that_exited(world) -> None:
    world.service.start(GOOD_ARGS, world.user)
    world.recorder.process.exit_code = 9
    assert world.service.status() == {"ok": True, "running": False, "exited_with": 9}
    assert "nft-remove" in world.recorder.events


def test_cleanup_removes_the_table_and_kills_only_a_leftover_engine_of_ours(world) -> None:
    state = world.tmp_path / "state.json"
    state.write_text(json.dumps({"pid": 777, "sysctls": {"net.netfilter.nf_conntrack_tcp_be_liberal": "0"}}))
    world.recorder.sysctls["net.netfilter.nf_conntrack_tcp_be_liberal"] = "1"
    world.recorder.alive_pids = {777}
    world.recorder.engine_pids = set()  # pid 777 now belongs to some other program: leave it alone
    answer = world.service.cleanup_stale()
    assert answer == {"ok": True, "removed_pid": None}
    assert world.recorder.killed == []
    assert "nft-remove" in world.recorder.events
    assert world.recorder.sysctls["net.netfilter.nf_conntrack_tcp_be_liberal"] == "0"
    assert not state.exists()

    state.write_text(json.dumps({"pid": 778, "sysctls": {}}))
    world.recorder.alive_pids = {778}
    world.recorder.engine_pids = {778}
    answer = world.service.cleanup_stale()
    assert answer["removed_pid"] == 778
    assert (778, signal.SIGTERM) in world.recorder.killed


def test_cleanup_without_a_state_file_still_removes_the_table(world) -> None:
    assert world.service.cleanup_stale() == {"ok": True, "removed_pid": None}
    assert world.recorder.events == ["nft-remove"]


# -- the socket and the client -----------------------------------------------------------------------


@pytest.fixture()
def running_server(world, short_dir):
    path = str(short_dir / "helper.sock")
    stop = threading.Event()
    allowed = {"yes": True}

    def authorize(_conn):
        return world.user if allowed["yes"] else None

    listener = server.open_socket(path, group_name=None, mode=0o600)
    thread = threading.Thread(
        target=server.serve, args=(world.service,), kwargs={"path": path, "authorize": authorize, "stop_event": stop, "server": listener}, daemon=True
    )
    thread.start()
    yield path, allowed, world
    stop.set()
    thread.join(5)


def test_a_client_can_start_ask_and_stop_through_the_socket(running_server) -> None:
    path, _allowed, world = running_server
    client = helper_client.HelperClient(path, timeout=5)
    assert client.status() == {"ok": True, "running": False}
    answer = client.start(GOOD_ARGS, label="Preset")
    assert answer["ok"] and answer["pid"] == 4242
    assert client.status()["running"] is True
    assert client.stop()["ok"]
    assert client.status()["running"] is False


def test_somebody_outside_the_group_is_turned_away(running_server) -> None:
    path, allowed, _world = running_server
    allowed["yes"] = False
    with pytest.raises(helper_client.HelperUnavailable) as raised:
        helper_client.HelperClient(path, timeout=5).status()
    assert "usermod -aG zapretgui" in str(raised.value)


def test_without_the_helper_the_client_says_how_to_start_it(short_dir) -> None:
    with pytest.raises(helper_client.HelperUnavailable) as raised:
        helper_client.HelperClient(str(short_dir / "none.sock"), timeout=1).status()
    assert "systemctl start zapretgui-helper" in str(raised.value)


def test_a_socket_nobody_listens_on_is_unavailable_not_a_crash(short_dir) -> None:
    path = str(short_dir / "dead.sock")
    leftover = socket.socket(socket.AF_UNIX)
    leftover.bind(path)
    leftover.close()
    with pytest.raises(helper_client.HelperUnavailable):
        helper_client.HelperClient(path, timeout=1).status()


def test_stopping_the_server_removes_the_firewall_rules_and_the_socket(world, short_dir) -> None:
    path = str(short_dir / "h.sock")
    stop = threading.Event()
    listener = server.open_socket(path, group_name=None, mode=0o600)
    thread = threading.Thread(
        target=server.serve, args=(world.service,), kwargs={"path": path, "authorize": lambda _c: world.user, "stop_event": stop, "server": listener}, daemon=True
    )
    thread.start()
    helper_client.HelperClient(path, timeout=5).start(GOOD_ARGS)
    assert world.recorder.process.exit_code is None
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    assert world.recorder.process.exit_code == 0  # terminated on the way out
    assert world.recorder.events[-1] == "nft-remove" or "nft-remove" in world.recorder.events[-3:]
    assert not os.path.exists(path)


def test_the_server_starts_by_removing_leftovers_of_a_crashed_predecessor(world, short_dir) -> None:
    (world.tmp_path / "state.json").write_text(json.dumps({"pid": 5, "sysctls": {}}))
    path = str(short_dir / "h2.sock")
    stop = threading.Event()
    stop.set()  # serve() cleans up, then leaves immediately
    server.serve(world.service, path=path, authorize=lambda _c: world.user, stop_event=stop, server=server.open_socket(path, group_name=None))
    assert world.recorder.events[0] == "nft-remove"
    assert not (world.tmp_path / "state.json").exists()


def test_peer_credentials_come_from_the_kernel_not_from_the_message() -> None:
    a, b = socket.socketpair()
    uid, gid, pid = server.peer_credentials(a)
    assert uid == os.getuid() and gid == os.getgid() and pid == os.getpid()


def test_group_authorization_accepts_root_and_members_only(monkeypatch) -> None:
    import grp
    import pwd

    a, _b = socket.socketpair()
    me = pwd.getpwuid(os.getuid())
    monkeypatch.setattr(grp, "getgrnam", lambda name: grp.struct_group(("zapretgui", "x", 987654, [me.pw_name])))
    caller = server.authorize_by_group(a)
    assert caller is not None and caller.uid == os.getuid()
    monkeypatch.setattr(grp, "getgrnam", lambda name: grp.struct_group(("zapretgui", "x", 987654, ["somebody-else"])))
    if os.getuid() == 0:
        assert server.authorize_by_group(a) is not None  # root is always allowed
    else:
        assert server.authorize_by_group(a) is None
    monkeypatch.setattr(grp, "getgrnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
    if os.getuid() != 0:
        assert server.authorize_by_group(a) is None


def test_the_engine_of_a_root_caller_runs_as_nobody(world) -> None:
    root = Caller(uid=0, home="/root", gid=0)
    assert world.service.start(GOOD_ARGS, root)["ok"]
    assert "--uid=65534:65534" in world.recorder.spawned[0]


# -- the engine the helper runs is found through the engine interface, not by a program name -------------------


def test_the_linux_engine_reports_the_helpers_engine_pid(monkeypatch) -> None:
    from platforms.linux.engine import LinuxEngine

    class Client:
        def __init__(self, answer):
            self.answer = answer

        def status(self):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    for answer, expected in (
        ({"ok": True, "running": True, "pid": 4321}, [4321]),
        ({"ok": True, "running": False}, []),
        (helper_client.HelperUnavailable("not running"), []),
    ):
        monkeypatch.setattr(helper_client, "HelperClient", lambda answer=answer: Client(answer))
        assert LinuxEngine().running_pids() == expected


def test_the_process_scan_asks_the_engine_where_the_engine_is_not_found_by_name(monkeypatch) -> None:
    """Found on a real Linux desktop: the tray said "stopped" while nfqws2 ran, because the scan looked for winws2.exe."""
    from platforms import Cap
    from platforms.stub import make_platform
    from winws_runtime.runtime import process_probe

    class Engine:
        def running_pids(self):
            return [4321]

    monkeypatch.setattr(process_probe, "current_platform", lambda: make_platform(engine=Engine(), capabilities=frozenset()))
    assert process_probe.scan_winws_processes().canonical_pids == {"winws2.exe": [4321]}
    assert [r.pid for r in process_probe.find_expected_winws_processes("/opt/zapretgui/exe/winws2.exe")] == [4321]
    assert process_probe.find_expected_winws_processes("/opt/zapretgui/exe/winws.exe") == []
    assert process_probe.get_canonical_winws_process_pids() == {"winws2.exe": [4321]}
    # Windows keeps the scan by name: the engine interface is not asked
    monkeypatch.setattr(process_probe, "current_platform", lambda: make_platform(engine=Engine(), capabilities=frozenset({Cap.DIRECT_ENGINE_PROCESS})))
    monkeypatch.setattr(process_probe, "_iter_winws_process_entries", lambda: [])
    assert process_probe.scan_winws_processes().canonical_pids == {}


def test_the_windivert_driver_cleanup_is_skipped_where_there_is_no_driver(monkeypatch) -> None:
    """Seen in the log of a real Linux desktop: every stop warned "Не удалось прочитать службу драйвера WinDivert"."""
    from platforms import Cap
    from platforms.stub import make_platform
    from winws_runtime.runtime import runtime_api

    calls = []
    monkeypatch.setattr(runtime_api, "release_windivert_driver_runtime", lambda: calls.append(1) or SimpleNamespace(stuck=False, message=""))
    api = runtime_api.PresetLaunchRuntimeApi("/opt/zapretgui/exe/winws2.exe")
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset()))
    assert api.cleanup_windivert_service() is True and calls == []
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset({Cap.DIRECT_ENGINE_PROCESS})))
    api.cleanup_windivert_service()
    assert calls == [1]
