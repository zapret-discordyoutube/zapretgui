"""The Linux runner: relative preset paths become absolute, Linux limits are reported early, the helper does the work."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config.runtime_layout import ApplicationPaths
from platforms.linux.helper.client import HelperUnavailable
from platforms.linux.runner import LinuxStrategyRunner, absolute_engine_args, resolve_relative

pytestmark = pytest.mark.real_engine


@pytest.fixture()
def paths(tmp_path):
    return ApplicationPaths.for_xdg_layout(tmp_path / "opt", {"HOME": str(tmp_path / "home")})


class FakeClient:
    def __init__(self, **answers) -> None:
        self.calls: list[tuple] = []
        self.answers = {"start": {"ok": True, "pid": 321}, "stop": {"ok": True}, "status": {"ok": True, "running": False}}
        self.answers.update(answers)

    def _answer(self, op):
        value = self.answers[op]
        if isinstance(value, Exception):
            raise value
        return value

    def start(self, args, *, label=""):
        self.calls.append(("start", list(args), label))
        return self._answer("start")

    def stop(self):
        self.calls.append(("stop",))
        return self._answer("stop")

    def status(self):
        self.calls.append(("status",))
        return self._answer("status")


def _preset(tmp_path, lines) -> str:
    path = tmp_path / "preset.txt"
    path.write_text("# Preset: test\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


GOOD = ["--lua-init=@lua/zapret-lib.lua", "--blob=tls1:@bin/tls_clienthello_1.bin", "--wf-tcp-out=80,443", "--wf-udp-out=443",
        "--filter-tcp=443", "--hostlist=lists/youtube.txt", "--ipset-exclude=lists/base/skip.txt", "--lua-desync=fake:blob=tls1"]


# -- path rewriting ---------------------------------------------------------------------------------


def test_every_tree_folder_maps_to_its_place_in_the_xdg_layout(paths) -> None:
    assert resolve_relative("bin/a.bin", paths) == str(paths.root / "bin" / "a.bin")
    assert resolve_relative("lua/x.lua", paths) == str(paths.root / "lua" / "x.lua")
    assert resolve_relative("lists/youtube.txt", paths) == str(paths.data_root / "lists" / "youtube.txt")
    assert resolve_relative("lists/user/mine.txt", paths) == str(paths.data_root / "lists" / "user" / "mine.txt")
    assert resolve_relative("lists/base/skip.txt", paths) == str(paths.root / "lists" / "base" / "skip.txt")
    assert resolve_relative("user/fakes/f.bin", paths) == str(paths.data_root / "fakes" / "f.bin")
    assert resolve_relative("elsewhere/x", paths) is None and resolve_relative("justfile", paths) is None
    assert resolve_relative("/abs/path", paths) is None


def test_arguments_get_absolute_files_and_everything_else_stays(paths) -> None:
    result = absolute_engine_args(GOOD, paths)
    assert f"--lua-init=@{paths.root}/lua/zapret-lib.lua" in result
    assert f"--blob=tls1:@{paths.root}/bin/tls_clienthello_1.bin" in result
    assert f"--hostlist={paths.data_root}/lists/youtube.txt" in result
    assert f"--ipset-exclude={paths.root}/lists/base/skip.txt" in result
    for kept in ("--wf-tcp-out=80,443", "--filter-tcp=443", "--lua-desync=fake:blob=tls1"):
        assert kept in result


def test_capture_options_with_files_are_left_for_the_helper_to_read(paths) -> None:
    assert absolute_engine_args(["--wf-raw-part=@windivert.filter/part.stun.txt"], paths) == ["--wf-raw-part=@windivert.filter/part.stun.txt"]


def test_a_lua_code_string_and_unknown_folders_are_not_touched(paths) -> None:
    assert absolute_engine_args(["--lua-init=function f() end", "--hostlist=weird/place.txt"], paths) == ["--lua-init=function f() end", "--hostlist=weird/place.txt"]


# -- early validation -------------------------------------------------------------------------------


def test_a_preset_the_translator_accepts_is_valid(tmp_path, paths) -> None:
    runner = LinuxStrategyRunner("nfqws2", client=FakeClient(), paths=paths)
    assert runner.validate_preset_file(_preset(tmp_path, GOOD)) == (True, "")


def test_unsupported_capture_options_are_named_before_anything_is_stopped(tmp_path, paths) -> None:
    runner = LinuxStrategyRunner("nfqws2", client=FakeClient(), paths=paths)
    ok, report = runner.validate_preset_file(_preset(tmp_path, ["--wf-raw-part=@windivert.filter/nope.txt", "--wf-tcp-out=443"]))
    assert not ok and "не поддерживаются в Linux" in report and "nope.txt" in report


def test_a_preset_without_capture_options_is_not_valid(tmp_path, paths) -> None:
    ok, report = LinuxStrategyRunner("nfqws2", client=FakeClient(), paths=paths).validate_preset_file(_preset(tmp_path, ["--filter-tcp=443"]))
    assert not ok and "--wf-" in report


def test_an_unreadable_preset_is_reported_not_raised(tmp_path, paths) -> None:
    ok, report = LinuxStrategyRunner("nfqws2", client=FakeClient(), paths=paths).validate_preset_file(str(tmp_path / "missing.txt"))
    assert not ok and report


# -- starting and stopping --------------------------------------------------------------------------


def test_start_hands_the_absolute_arguments_to_the_helper(tmp_path, paths) -> None:
    client = FakeClient()
    runner = LinuxStrategyRunner("nfqws2", client=client, paths=paths)
    assert runner.start_from_preset_file(_preset(tmp_path, GOOD), "Мой пресет") is True
    op, args, label = client.calls[-1]
    assert op == "start" and label == "Мой пресет"
    assert f"--lua-init=@{paths.root}/lua/zapret-lib.lua" in args and "--wf-tcp-out=80,443" in args
    assert runner.get_runner_state_snapshot().pid == 321
    assert runner.get_current_strategy_info()["name"] == "Мой пресет"
    assert runner.last_error_message == ""


def test_a_refusal_of_the_helper_is_an_error_message_and_a_failed_start(tmp_path, paths) -> None:
    seen: list[str] = []
    client = FakeClient(start={"ok": False, "error": "the engine is not installed: /opt/zapretgui/bin/nfqws2"})
    runner = LinuxStrategyRunner("nfqws2", client=client, paths=paths)
    runner.configure_runtime_callbacks(launch_error=seen.append)
    assert runner.start_from_preset_file(_preset(tmp_path, GOOD)) is False
    assert "not installed" in runner.last_error_message and seen == [runner.last_error_message]
    assert runner.get_runner_state_snapshot().pid is None


def test_a_missing_helper_says_how_to_start_it(tmp_path, paths) -> None:
    client = FakeClient(start=HelperUnavailable("The Zapret helper service is not running. Start it with: sudo systemctl start zapretgui-helper"))
    runner = LinuxStrategyRunner("nfqws2", client=client, paths=paths)
    assert runner.start_from_preset_file(_preset(tmp_path, GOOD)) is False
    assert "systemctl start zapretgui-helper" in runner.last_error_message


def test_nothing_is_sent_when_the_preset_cannot_work_on_linux(tmp_path, paths) -> None:
    client = FakeClient()
    runner = LinuxStrategyRunner("nfqws2", client=client, paths=paths)
    assert runner.start_from_preset_file(_preset(tmp_path, ["--wf-raw-part=@windivert.filter/nope.txt", "--wf-tcp-out=443"])) is False
    assert client.calls == []


def test_a_switch_of_an_outdated_request_does_nothing(tmp_path, paths) -> None:
    client = FakeClient()
    runner = LinuxStrategyRunner("nfqws2", client=client, paths=paths)
    assert runner.switch_preset_file_fast(_preset(tmp_path, GOOD), "x", is_current=lambda: False) is True
    assert client.calls == []
    assert runner.switch_preset_file_fast(_preset(tmp_path, GOOD), "x", is_current=lambda: True) is True
    assert client.calls[-1][0] == "start"


def test_stop_asks_the_helper_and_forgets_the_run(tmp_path, paths) -> None:
    client = FakeClient()
    runner = LinuxStrategyRunner("nfqws2", client=client, paths=paths)
    runner.start_from_preset_file(_preset(tmp_path, GOOD), "P")
    assert runner.stop() is True
    assert client.calls[-1] == ("stop",)
    assert runner.get_current_strategy_info()["name"] is None


def test_stop_without_a_helper_is_fine_because_nothing_can_be_running(paths) -> None:
    runner = LinuxStrategyRunner("nfqws2", client=FakeClient(stop=HelperUnavailable("down")), paths=paths)
    assert runner.stop() is True


def test_is_running_follows_the_helper_and_survives_its_absence(paths) -> None:
    assert LinuxStrategyRunner("n", client=FakeClient(status={"ok": True, "running": True}), paths=paths).is_running() is True
    assert LinuxStrategyRunner("n", client=FakeClient(status={"ok": True, "running": False}), paths=paths).is_running() is False
    assert LinuxStrategyRunner("n", client=FakeClient(status=HelperUnavailable("down")), paths=paths).is_running() is False


def test_a_restarted_window_adopts_the_engine_the_helper_kept(paths) -> None:
    client = FakeClient(status={"ok": True, "running": True, "pid": 99, "label": "Старый пресет"})
    runner = LinuxStrategyRunner("n", client=client, paths=paths)
    assert runner.adopt_running_engine() is True
    assert runner.get_runner_state_snapshot().pid == 99 and runner.get_current_strategy_info()["name"] == "Старый пресет"
    assert LinuxStrategyRunner("n", client=FakeClient(), paths=paths).adopt_running_engine() is False


def test_the_surface_the_program_calls_exists_and_is_harmless(paths) -> None:
    runner = LinuxStrategyRunner("n", client=FakeClient(), paths=paths)
    runner.stop_extra_adopted_engines()
    runner.stop_background_watchers()
    assert runner.get_process() is None and runner.build_post_mortem_snapshot() is None
    assert isinstance(runner.read_post_mortem_output(), str)
    assert runner.launch_transition_in_progress("direct_zapret2") is False
    runner.configure_runtime_callbacks(transition_in_progress=lambda method: True, unexpected_process_exit=None)
    assert runner.launch_transition_in_progress("direct_zapret2") is True
