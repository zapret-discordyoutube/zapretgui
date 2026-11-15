"""Privileges of each backend, and the Linux single-instance (lock file + socket)."""

from __future__ import annotations

from platforms.stub import make_platform

import ctypes
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from platforms.linux.privileges import LinuxPrivileges
from platforms.stub import PLATFORM as STUB
from platforms.windows.privileges import WindowsPrivileges


# --- privileges -----------------------------------------------------------

def test_windows_privileges_report_the_admin_check():
    fake = MagicMock()
    with patch.object(ctypes, "windll", fake, create=True):
        fake.shell32.IsUserAnAdmin.return_value = 1
        assert WindowsPrivileges().is_elevated() is True
        fake.shell32.IsUserAnAdmin.return_value = 0
        assert WindowsPrivileges().is_elevated() is False
        fake.shell32.IsUserAnAdmin.side_effect = OSError("no shell32")
        assert WindowsPrivileges().is_elevated() is False


def test_windows_relaunch_asks_for_runas_with_a_quoted_command_line():
    fake = MagicMock()
    fake.shell32.ShellExecuteW.return_value = 42
    with patch.object(ctypes, "windll", fake, create=True):
        accepted = WindowsPrivileges().relaunch_elevated(r"C:\Zapret\_internal\Zapret.exe", ["--tray", "a b"])
    assert accepted is True
    fake.shell32.ShellExecuteW.assert_called_once_with(
        None, "runas", r"C:\Zapret\_internal\Zapret.exe", '--tray "a b"', None, 1
    )


def test_windows_relaunch_refused_when_shellexecute_reports_an_error_code():
    fake = MagicMock()
    fake.shell32.ShellExecuteW.return_value = 5  # ERROR_ACCESS_DENIED, <= 32
    with patch.object(ctypes, "windll", fake, create=True):
        assert WindowsPrivileges().relaunch_elevated("x.exe", []) is False


def test_only_windows_needs_an_elevated_window():
    assert WindowsPrivileges.gui_needs_elevation is True
    assert LinuxPrivileges.gui_needs_elevation is False
    assert STUB.privileges.gui_needs_elevation is False


def test_linux_privileges_follow_the_effective_user():
    with patch("platforms.linux.privileges.os.geteuid", return_value=0):
        assert LinuxPrivileges().is_elevated() is True
    with patch("platforms.linux.privileges.os.geteuid", return_value=1000):
        assert LinuxPrivileges().is_elevated() is False
    assert LinuxPrivileges().relaunch_elevated("x", []) is False


def test_stub_privileges_say_no():
    assert STUB.privileges.is_elevated() is False
    assert STUB.privileges.relaunch_elevated("x", []) is False


def test_shell_does_not_ask_for_elevation_when_the_platform_does_not_need_it():
    from main import shell
    from platforms.base import InstanceLock, Platform

    privileges = MagicMock(gui_needs_elevation=False)
    privileges.is_elevated.return_value = False
    single = MagicMock()
    single.acquire.return_value = InstanceLock(already_running=False)
    platform = make_platform(name="fake", messages=MagicMock(), privileges=privileges, single_instance=single,
                        services=MagicMock(), launch=MagicMock())
    with (
        patch.object(shell, "current_platform", return_value=platform),
        patch.object(shell, "register_exit_step"),
        patch.object(shell, "_update_is_being_installed", return_value=False),
    ):
        shell.shell_bootstrap(["app"])
    privileges.relaunch_elevated.assert_not_called()


def test_shell_exits_with_a_message_when_the_elevation_request_is_refused():
    from main import shell
    from platforms.base import Platform

    messages = MagicMock()
    privileges = MagicMock(gui_needs_elevation=True)
    privileges.is_elevated.return_value = False
    privileges.relaunch_elevated.return_value = False
    platform = make_platform(name="fake", messages=messages, privileges=privileges, single_instance=MagicMock(),
                        services=MagicMock(), launch=MagicMock())
    with (
        patch.object(shell, "current_platform", return_value=platform),
        patch.object(shell, "_update_is_being_installed", return_value=False),
        pytest.raises(SystemExit) as exit_info,
    ):
        shell.shell_bootstrap(["app", "--tray"])
    assert exit_info.value.code == 0
    messages.show.assert_called_once()
    privileges.relaunch_elevated.assert_called_once()
    assert privileges.relaunch_elevated.call_args.args[1] == ["--tray"]


# --- Linux single instance --------------------------------------------------

pytest.importorskip("fcntl")
from platforms.linux.single_instance import SHOW_SOCKET_NAME, LinuxSingleInstance  # noqa: E402


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_second_copy_is_seen_while_the_first_holds_the_lock(runtime):
    first = LinuxSingleInstance().acquire("ZapretSingleInstance")
    second = LinuxSingleInstance().acquire("ZapretSingleInstance")
    assert first.already_running is False
    assert second.already_running is True
    first.release()


def test_lock_is_free_again_after_release(runtime):
    LinuxSingleInstance().acquire("ZapretSingleInstance").release()
    again = LinuxSingleInstance().acquire("ZapretSingleInstance")
    assert again.already_running is False
    again.release()


def test_unwritable_runtime_folder_means_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr("platforms.linux.single_instance.tempfile.gettempdir", lambda: str(tmp_path / "also-missing" / "x"))
    assert LinuxSingleInstance().acquire("ZapretSingleInstance").already_running is False


def test_signal_reaches_the_watcher_even_before_it_starts(runtime):
    first = LinuxSingleInstance()
    lock = first.acquire("ZapretSingleInstance")
    assert first.create_show_signal() is True

    # The second copy signals while the first one is still "loading" (no watcher yet).
    assert LinuxSingleInstance().signal_running_instance() is True

    shown = threading.Event()
    thread = first.start_show_watcher(shown.set)
    assert thread is not None
    assert shown.wait(timeout=5), "the early signal was lost"
    lock.release()


def test_every_signal_calls_back_once(runtime):
    first = LinuxSingleInstance()
    first.acquire("ZapretSingleInstance")
    first.create_show_signal()
    count = []
    got_two = threading.Event()

    def on_show():
        count.append(1)
        if len(count) == 2:
            got_two.set()

    first.start_show_watcher(on_show)
    second = LinuxSingleInstance()
    assert second.signal_running_instance() and second.signal_running_instance()
    assert got_two.wait(timeout=5)


def test_signal_without_a_running_copy_is_false(runtime):
    assert LinuxSingleInstance().signal_running_instance() is False


def test_leftover_socket_of_a_dead_copy_does_not_block_a_new_one(runtime):
    leftover = runtime / SHOW_SOCKET_NAME
    leftover.write_text("stale")
    instance = LinuxSingleInstance()
    instance.acquire("ZapretSingleInstance")
    assert instance.create_show_signal() is True


def test_watcher_needs_the_signal_to_exist(runtime):
    assert LinuxSingleInstance().start_show_watcher(lambda: None) is None
