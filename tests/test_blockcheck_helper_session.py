"""The strategy search on a system where the root helper starts the engine."""
from __future__ import annotations

import pytest

from blockcheck.strategy_search.helper_session import HelperSession
from blockcheck.strategy_search.winws_session import LUA_SETTLE_SECONDS


class FakeRunner:
    def __init__(self, start_ok=True, running=True, error=""):
        self.start_ok, self.running, self.last_error_message = start_ok, running, error
        self.started, self.stopped = [], 0

    def start_from_preset_file(self, path, name="x", **_):
        self.started.append((path, name))
        return self.start_ok

    def is_running(self):
        return self.running

    def stop(self, **_):
        self.stopped += 1
        self.running = False
        return True


def session(runner, **kw):
    sleeps = []
    clock = iter(range(100)).__next__
    made = HelperSession("/tmp/probe.conf", runner_getter=lambda: runner, clock=lambda: float(clock()), sleep=sleeps.append, **kw)
    return made, sleeps


def test_a_started_probe_is_ready_after_the_lua_pause_and_reports_it_alive():
    runner = FakeRunner()
    probe, sleeps = session(runner)
    result = probe.start()
    assert result.ok and result.ready_ms > 0 and sleeps == [LUA_SETTLE_SECONDS]
    assert runner.started == [("/tmp/probe.conf", "BlockCheck")] and probe.alive() is True


def test_a_refused_start_returns_the_reason_from_the_helper():
    probe, _ = session(FakeRunner(start_ok=False, error="not supported on Linux: --wf-save"))
    result = probe.start()
    assert result.ok is False and "not supported on Linux" in result.output_tail and probe.alive() is False


def test_an_engine_that_dies_at_once_is_a_failed_start_not_a_ready_one():
    probe, _ = session(FakeRunner(running=False, error="lua: bad strategy"))
    result = probe.start()
    assert result.ok is False and result.exit_code == 1 and "bad strategy" in result.output_tail


def test_a_cancelled_search_does_not_start_the_engine():
    runner = FakeRunner()
    probe, _ = session(runner, cancelled=lambda: True)
    assert probe.start().ok is False and runner.started == []


def test_stop_stops_the_runner_once_and_is_harmless_before_a_start():
    runner = FakeRunner()
    probe, _ = session(runner)
    assert probe.stop() is True and runner.stopped == 0
    probe.start()
    assert probe.stop() is True and probe.stop() is True and runner.stopped == 1


def test_environment_uses_the_helper_session_without_the_direct_engine_capability(tmp_path, monkeypatch):
    from blockcheck.strategy_search import environment
    from platforms.stub import make_platform

    runner = FakeRunner()

    class Engine:
        def current_runner(self):
            return runner

        def runner(self, path):
            raise AssertionError("an existing runner must be reused")

    monkeypatch.setattr("platforms.current", lambda: make_platform(engine=Engine(), capabilities=frozenset()))
    env = environment.RealEnvironment.__new__(environment.RealEnvironment)
    env._root = str(tmp_path)

    class Cancel:
        cancelled = False

    env._cancel = Cancel()
    managed = env.start_session("--lua-init=@lua/x.lua\n")
    assert (tmp_path / environment.PROBE_CONFIG_NAME).read_text(encoding="utf-8") == "--lua-init=@lua/x.lua\n"
    assert managed.start().ok is True and runner.started[0][0] == str(tmp_path / environment.PROBE_CONFIG_NAME)
    assert managed.stop() is True


def test_recovery_after_a_crash_stops_the_helper_runner_not_windows_processes(monkeypatch):
    from blockcheck.strategy_search import environment
    from platforms.stub import make_platform

    runner = FakeRunner()

    class Engine:
        def current_runner(self):
            return runner

    monkeypatch.setattr("platforms.current", lambda: make_platform(engine=Engine(), capabilities=frozenset()))
    env = environment.RealEnvironment.__new__(environment.RealEnvironment)
    env.strategy_pause_seconds = lambda: 0

    class Cancel:
        def sleep(self, seconds):
            pass

    env._cancel = Cancel()
    env.recover_after_crash()
    assert runner.stopped == 1 and env._needs_readiness_check is True


def test_windows_keeps_the_direct_process_capability():
    from platforms import Cap
    from platforms.linux import PLATFORM as linux
    from platforms.windows import PLATFORM as windows

    assert windows.has(Cap.DIRECT_ENGINE_PROCESS) and not linux.has(Cap.DIRECT_ENGINE_PROCESS)
