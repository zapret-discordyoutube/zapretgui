"""The message box of each platform backend, and the launch code that uses it."""

from __future__ import annotations

import ctypes
import subprocess
from unittest.mock import MagicMock, patch

import platforms
from platforms import MessageKind
from platforms.linux.messages import LinuxMessageBox
from platforms.stub import PLATFORM as STUB
from platforms.windows.messages import WindowsMessageBox


def test_current_platform_is_chosen_from_sys_platform():
    for sys_platform, expected in (("win32", "windows"), ("linux", "linux"), ("darwin", "stub")):
        with patch.object(platforms.sys, "platform", sys_platform):
            assert platforms._select().name == expected


def test_current_platform_is_chosen_once():
    with patch.object(platforms, "_current", None):
        first = platforms.current()
        assert platforms.current() is first


def test_windows_message_box_uses_the_icon_of_the_kind():
    fake = MagicMock()
    with patch.object(ctypes, "windll", fake, create=True):
        for kind, flag in ((MessageKind.INFO, 0x40), (MessageKind.WARNING, 0x30), (MessageKind.ERROR, 0x10)):
            WindowsMessageBox().show("T", "M", kind)
            fake.user32.MessageBoxW.assert_called_with(None, "M", "T", flag)


def test_windows_message_box_falls_back_to_the_terminal(capsys):
    fake = MagicMock()
    fake.user32.MessageBoxW.side_effect = OSError("no window station")
    with patch.object(ctypes, "windll", fake, create=True):
        WindowsMessageBox().show("Title", "Text")
    assert "Title" in capsys.readouterr().err


def test_linux_message_box_without_a_desktop_prints(monkeypatch, capsys):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with patch("platforms.linux.messages.subprocess.run") as run:
        LinuxMessageBox().show("Title", "Text")
    run.assert_not_called()
    assert "Text" in capsys.readouterr().err


def test_linux_message_box_prefers_zenity(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    with (
        patch("platforms.linux.messages.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
        patch("platforms.linux.messages.subprocess.run") as run,
    ):
        LinuxMessageBox().show("Title", "Text", MessageKind.WARNING)
    command = run.call_args.args[0]
    assert command[:2] == ["/usr/bin/zenity", "--warning"]
    assert command[-1] == "Text" and "Title" in command


def test_linux_message_box_uses_kdialog_when_zenity_is_missing(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    with (
        patch("platforms.linux.messages.shutil.which", side_effect=lambda n: "/usr/bin/kdialog" if n == "kdialog" else None),
        patch("platforms.linux.messages.subprocess.run") as run,
    ):
        LinuxMessageBox().show("Title", "Text", MessageKind.ERROR)
    assert run.call_args.args[0][:3] == ["/usr/bin/kdialog", "--error", "Text"]


def test_linux_message_box_falls_back_when_the_program_cannot_start(monkeypatch, capsys):
    monkeypatch.setenv("DISPLAY", ":0")
    with (
        patch("platforms.linux.messages.shutil.which", return_value="/usr/bin/zenity"),
        patch("platforms.linux.messages.subprocess.run", side_effect=OSError("exec failed")),
    ):
        LinuxMessageBox().show("Title", "Text")
    assert "Text" in capsys.readouterr().err


def test_stub_message_box_prints(capsys):
    STUB.messages.show("Title", "Text")
    assert "Title" in capsys.readouterr().err


def test_launch_error_goes_through_the_platform():
    from main import launch_gate

    box = MagicMock()
    with patch.object(launch_gate, "current_platform", return_value=MagicMock(messages=box)):
        launch_gate.show_launch_error("T", "M")
    box.show.assert_called_once_with("T", "M", MessageKind.ERROR)
