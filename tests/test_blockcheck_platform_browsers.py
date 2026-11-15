"""BlockCheck browsers and processes behind the platform interfaces."""
from __future__ import annotations

import os
import subprocess

import pytest

from blockcheck import browser_check, browser_switch
from platforms import Cap
from platforms.linux.processes import LinuxProcesses
from platforms.linux.system_settings import LinuxSystemSettings
from platforms.stub import make_platform
from platforms.windows.processes import WindowsProcesses
from platforms.windows.system_settings import WindowsSystemSettings


def test_the_browser_lookup_asks_the_platform(monkeypatch):
    class Settings:
        def browser_paths(self):
            return ["/usr/bin/microsoft-edge", "/usr/bin/google-chrome"]

    monkeypatch.setattr("platforms.current", lambda: make_platform(system_settings=Settings()))
    assert browser_check.find_browsers() == ["/usr/bin/microsoft-edge", "/usr/bin/google-chrome"]
    assert browser_check.find_browser() == "/usr/bin/microsoft-edge"


def test_linux_finds_the_chromium_family_edge_first_without_duplicates(tmp_path, monkeypatch):
    real = tmp_path / "chrome-real"
    real.write_text("#!/bin/sh\n")
    (tmp_path / "google-chrome").symlink_to(real)
    (tmp_path / "google-chrome-stable").symlink_to(real)  # the same program under a second name
    edge = tmp_path / "msedge-real"
    edge.write_text("#!/bin/sh\n")
    (tmp_path / "microsoft-edge").symlink_to(edge)
    monkeypatch.setattr("shutil.which", lambda name: str(tmp_path / name) if (tmp_path / name).exists() else None)
    assert LinuxSystemSettings().browser_paths() == [str(edge), str(real)]


@pytest.mark.skipif(os.name != "nt", reason="the program folders are Windows paths with %VARIABLES%")
def test_windows_expands_the_program_folders_and_skips_what_is_missing(tmp_path, monkeypatch):
    edge = tmp_path / "pf" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("x")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("LocalAppData", str(tmp_path / "local"))
    assert WindowsSystemSettings().browser_paths() == [str(edge)]


@pytest.mark.parametrize(
    ("name", "title"),
    [("msedge.exe", "Edge"), ("chrome.exe", "Chrome"), ("microsoft-edge", "Edge"), ("google-chrome-stable", "Chrome"), ("chromium", "Chromium"), ("zzz", "zzz")],
)
def test_browser_titles_cover_both_systems(name, title):
    assert browser_check.browser_title(name) == title


def test_the_hidden_process_options_fit_each_system(monkeypatch):
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert WindowsProcesses().hidden_process_options() == {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    assert LinuxProcesses().hidden_process_options() == {"start_new_session": True}
    # a Linux child really becomes the leader of its own group
    child = subprocess.Popen(["sleep", "5"], **LinuxProcesses().hidden_process_options())
    try:
        assert os.getpgid(child.pid) == child.pid
    finally:
        child.kill()
        child.wait()


def test_browser_launch_uses_the_platform_options(monkeypatch):
    seen = {}

    class Processes:
        def hidden_process_options(self):
            return {"start_new_session": True}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("platforms.current", lambda: make_platform(processes=Processes()))
    monkeypatch.setattr(browser_check.subprocess, "Popen", FakePopen)
    browser_check._hidden_popen(["chromium"], None)
    assert seen["start_new_session"] is True and "creationflags" not in seen


def test_the_policy_switches_exist_only_where_the_registry_policy_does(monkeypatch):
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset()))
    assert browser_switch.can_switch("chrome") is False and browser_switch.can_switch("firefox") is False
    monkeypatch.setattr("platforms.current", lambda: make_platform(capabilities=frozenset({Cap.BROWSER_POLICY_SWITCH})))
    assert browser_switch.can_switch("chrome") is True and browser_switch.can_switch("firefox") is True
    assert browser_switch.can_switch("opera") is False


def test_windows_has_the_policy_switch_and_linux_does_not():
    from platforms.linux import PLATFORM as linux
    from platforms.windows import PLATFORM as windows

    assert windows.has(Cap.BROWSER_POLICY_SWITCH) and not linux.has(Cap.BROWSER_POLICY_SWITCH)
