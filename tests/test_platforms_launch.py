"""The ``launch`` interface: process start time, system support, the source-start message."""

from __future__ import annotations

import sys
import time

import pytest

from platforms import current
from platforms.base import SystemSupport
from platforms.linux.launch import DEV_ROOT_ENV, LinuxLaunch
from platforms.stub import PLATFORM as STUB
from platforms.windows.launch import WindowsLaunch, filetime_to_unix_seconds


def test_every_backend_exposes_launch() -> None:
    from platforms.linux import PLATFORM as LINUX
    from platforms.windows import PLATFORM as WINDOWS

    for backend in (WINDOWS, LINUX, STUB):
        launch = backend.launch
        assert isinstance(launch.system_support(), SystemSupport)
        assert isinstance(launch.source_launch_message(), str) and launch.source_launch_message()


def test_unsupported_system_carries_the_window_text() -> None:
    from platforms.windows import system_support as guard

    class Old:
        major, minor, build = 6, 1, 0

    result = guard.current_windows_support(platform_name="win32", version_getter=lambda: Old())
    assert not result.supported
    from unittest.mock import patch

    with patch("platforms.windows.launch.current_windows_support", return_value=result):
        support = WindowsLaunch().system_support()
    assert support == SystemSupport(
        supported=False, os_name="Windows 7", title=guard.WINDOWS_VERSION_ERROR_TITLE, message=result.message
    )


def test_supported_windows_has_no_window_text() -> None:
    support = WindowsLaunch().system_support()
    # On Linux the Windows guard answers "supported" (it only judges win32).
    assert support.supported and not support.message


def test_windows_source_message_names_the_installed_exe() -> None:
    assert "_internal\\Zapret.exe" in WindowsLaunch().source_launch_message()


def test_linux_source_message_names_the_dev_switch() -> None:
    message = LinuxLaunch().source_launch_message()
    assert DEV_ROOT_ENV in message and "prepare_dev_root" in message


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc")
def test_linux_process_start_is_in_the_past_and_matches_psutil() -> None:
    import psutil

    created = LinuxLaunch().process_creation_unix_seconds()
    assert created is not None
    assert created <= time.time()
    assert created == pytest.approx(psutil.Process().create_time(), abs=2.0)


def test_linux_process_start_survives_spaces_in_the_command_name(monkeypatch, tmp_path) -> None:
    """The command name sits in brackets in /proc/self/stat and may contain spaces and ')'."""
    import builtins

    stat = "123 (my (odd) name) S " + " ".join(["0"] * 18) + " 5000 " + " ".join(["0"] * 20)
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/stat":
            f = tmp_path / "stat"
            f.write_bytes(stat.encode())
            return real_open(f, *args, **kwargs)
        if path == "/proc/stat":
            f = tmp_path / "procstat"
            f.write_bytes(b"cpu 1 2 3\nbtime 9900\nprocesses 5\n")
            return real_open(f, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    import os

    monkeypatch.setattr(os, "sysconf", lambda name: 100)
    # boot = 9900 (btime); start = 9900 + 5000/100 = 9950
    assert LinuxLaunch().process_creation_unix_seconds() == pytest.approx(9950.0)


def test_stub_has_no_start_time() -> None:
    assert STUB.launch.process_creation_unix_seconds() is None


def test_windows_start_time_is_filetime_based() -> None:
    assert filetime_to_unix_seconds(11_644_473_600 * 10_000_000) == 0.0


def test_main_process_start_time_asks_the_platform(monkeypatch) -> None:
    import main.process_start_time as pst

    monkeypatch.setattr(current().launch, "process_creation_unix_seconds", lambda: pst.python_entered_wall() - 1.5)
    assert pst.exe_to_python_ms() == 1500
    monkeypatch.setattr(current().launch, "process_creation_unix_seconds", lambda: None)
    assert pst.exe_to_python_ms() is None
