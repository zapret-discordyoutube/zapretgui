"""The ``processes`` and ``working_set`` interfaces: Linux backend, stub backend, and the neutral callers."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from platforms import Cap, current
from platforms.base import NoWorkingSet, ProcessSnapshotError
from platforms.linux.processes import LinuxProcesses
from platforms.stub import PLATFORM as STUB

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc and psutil")


@linux_only
def test_linux_lists_this_process_with_its_name() -> None:
    records = LinuxProcesses().list_processes()
    mine = [name for pid, name in records if pid == os.getpid()]
    assert mine and mine[0]
    assert LinuxProcesses().list_processes_strict()


def test_linux_strict_list_raises_on_failure_but_lenient_list_is_empty() -> None:
    procs = LinuxProcesses()
    with patch("psutil.process_iter", side_effect=RuntimeError("boom")):
        with pytest.raises(ProcessSnapshotError):
            procs.list_processes_strict()
        assert procs.list_processes() == []


@linux_only
def test_linux_command_line_exe_path_and_modules() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.3)
        procs = LinuxProcesses()
        assert "time.sleep(30)" in procs.command_line(child.pid)
        assert os.path.realpath(procs.exe_path(child.pid)) == os.path.realpath(sys.executable)
        assert any("libc" in path or "python" in os.path.basename(path) for path in procs.module_paths(child.pid))
        assert procs.command_line(2**22 + 12345) == ""
        assert procs.exe_path(2**22 + 12345) == ""
        assert procs.module_paths(2**22 + 12345) == []
    finally:
        child.kill()
        child.wait()


@linux_only
def test_linux_start_time_is_close_to_psutil_and_unknown_pid_is_zero() -> None:
    import psutil

    assert LinuxProcesses().start_time(os.getpid()) == pytest.approx(psutil.Process().create_time(), abs=0.01)
    assert LinuxProcesses().start_time(2**22 + 12345) == 0.0


@linux_only
def test_linux_kill_ends_a_process_and_confirms_the_exit() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(0.2)
    assert LinuxProcesses().kill(child.pid, wait_timeout_ms=3000) is True
    assert child.poll() is not None  # Popen reaps it; the process is really gone


@linux_only
def test_linux_kill_falls_back_to_sigkill_when_sigterm_is_ignored() -> None:
    code = "import signal,time,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    child.stdout.readline()  # the handler is installed
    started = time.monotonic()
    assert LinuxProcesses().kill(child.pid, wait_timeout_ms=2000) is True
    elapsed = time.monotonic() - started
    assert 0.9 < elapsed < 2.5  # SIGTERM was ignored for half of the time, then SIGKILL
    import psutil

    assert not psutil.pid_exists(child.pid)


def test_linux_kill_of_a_missing_process_is_false() -> None:
    assert LinuxProcesses().kill(2**22 + 12345) is False


def test_stub_processes_are_safe() -> None:
    procs = STUB.processes
    assert procs.list_processes() == []
    with pytest.raises(ProcessSnapshotError):
        procs.list_processes_strict()
    assert procs.start_time(1) == 0.0 and procs.kill(1) is False
    assert procs.command_line(1) == "" and procs.exe_path(1) == "" and procs.module_paths(1) == []


def test_no_working_set_answers_nothing() -> None:
    ws = NoWorkingSet()
    assert ws.snapshot_working_set() is None
    assert ws.prefetch_working_set(None) is None
    assert ws.hold_working_set(10) is None and ws.release_working_set_hold() is False
    assert ws.current_working_set_bytes() is None and ws.available_physical_bytes() is None
    assert ws.process_page_faults() is None


def test_every_backend_has_processes_and_working_set() -> None:
    from platforms.linux import PLATFORM as LINUX
    from platforms.windows import PLATFORM as WINDOWS

    for backend in (WINDOWS, LINUX, STUB):
        assert backend.processes is not None and backend.working_set is not None
    assert WINDOWS.has(Cap.WORKING_SET) and not LINUX.has(Cap.WORKING_SET) and not STUB.has(Cap.WORKING_SET)


def test_neutral_killer_uses_the_platform() -> None:
    from utils import process_killer

    fake = MagicMock()
    fake.list_processes.return_value = [(10, "Tool.EXE"), (11, "other.exe"), (12, "tool.exe")]
    fake.kill.return_value = True
    with patch("utils.process_killer.current_platform") as cp:
        cp.return_value.processes = fake
        assert process_killer.get_process_pids("tool.exe") == [10, 12]
        assert process_killer.kill_process_by_name("tool.exe") == 2
        assert process_killer.kill_process_by_name("tool.exe", kill_all=False) == 1
        assert process_killer.kill_process_by_pid(5, wait_timeout_ms=100) is True
        fake.kill.assert_called_with(5, wait_timeout_ms=100)


def test_window_presence_is_not_installed_without_the_capability() -> None:
    from ui import window_presence

    fake_platform = MagicMock()
    fake_platform.has.return_value = False
    with patch.object(window_presence, "current_platform", return_value=fake_platform):
        assert window_presence.install_window_presence(object()) is None
    fake_platform.has.assert_called_once_with(Cap.WORKING_SET)


def test_winws_probe_asks_the_platform_for_start_time_and_image_path() -> None:
    from winws_runtime.runtime import process_probe

    fake = MagicMock()
    fake.processes.start_time.return_value = 123.5
    fake.processes.exe_path.return_value = "\\\\?\\C:\\Z\\winws2.exe"
    with patch.object(process_probe, "current_platform", return_value=fake):
        assert process_probe.query_process_start_time(77) == 123.5
        assert process_probe._query_process_image_path(77) == process_probe._normalize_path("\\\\?\\C:\\Z\\winws2.exe")
    fake.processes.start_time.assert_called_once_with(77)
