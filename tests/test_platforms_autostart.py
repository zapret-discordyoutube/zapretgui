"""The ``autostart`` interface: Linux XDG entry, stub, and the neutral functions that call the platform."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autostart import public
from platforms.base import GuiAutostartResult
from platforms.linux import autostart as xdg
from platforms.linux.autostart import LinuxGuiAutostart
from platforms.stub import PLATFORM as STUB


@pytest.fixture()
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_enable_writes_a_valid_desktop_entry_in_the_user_folder(config_home) -> None:
    messages: list[str] = []
    result = LinuxGuiAutostart().enable("/opt/zapretgui/zapretgui", status_cb=messages.append)
    assert result.success and messages
    entry = config_home / "autostart" / "zapretgui.desktop"
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]\n")
    assert "Type=Application\n" in text and "Exec=/opt/zapretgui/zapretgui --tray\n" in text
    assert "Terminal=false\n" in text
    assert not list((config_home / "autostart").glob("*.tmp"))  # written through a temporary file and renamed


def test_exec_quotes_paths_with_spaces_and_special_characters() -> None:
    assert "Exec=\"/home/an na/Zapret GUI/zapretgui\" --tray\n" in xdg.render_entry("/home/an na/Zapret GUI/zapretgui")
    assert 'Exec="/tmp/a$b/z" --tray' in xdg.render_entry("/tmp/a$b/z").replace("\\$", "$")
    assert "%%" in xdg.render_entry("/tmp/100%/zapretgui")  # a field code must not be invented from a path


def test_registered_executable_reads_back_what_was_written(config_home) -> None:
    assert xdg.registered_executable() is None
    LinuxGuiAutostart().enable("/home/an na/zapretgui")
    assert xdg.registered_executable() == "/home/an na/zapretgui"


def test_disable_removes_the_entry_and_is_fine_when_there_is_none(config_home) -> None:
    assert LinuxGuiAutostart().disable() == GuiAutostartResult(success=True, removed_count=0)
    LinuxGuiAutostart().enable("/opt/zapretgui/zapretgui")
    assert LinuxGuiAutostart().disable() == GuiAutostartResult(success=True, removed_count=1)
    assert not (config_home / "autostart" / "zapretgui.desktop").exists()


def test_ensure_current_repairs_a_moved_program_and_leaves_a_good_entry_alone(config_home) -> None:
    probe = LinuxGuiAutostart()
    assert probe.ensure_current("/opt/zapretgui/zapretgui") is True  # nothing registered yet: registers
    entry = config_home / "autostart" / "zapretgui.desktop"
    first = entry.stat().st_mtime_ns
    assert probe.ensure_current("/opt/zapretgui/zapretgui") is False
    assert entry.stat().st_mtime_ns == first
    assert probe.ensure_current("/opt/new/zapretgui") is True
    assert xdg.registered_executable() == "/opt/new/zapretgui"


def test_enable_reports_a_folder_that_cannot_be_written(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))  # a file where a folder is needed
    result = LinuxGuiAutostart().enable("/opt/zapretgui/zapretgui")
    assert not result.success and "autostart" in result.message


def test_default_folder_is_dot_config_autostart(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(xdg.Path, "home", classmethod(lambda cls: tmp_path))
    assert xdg.autostart_dir() == tmp_path / ".config" / "autostart"


def test_stub_has_no_autostart_and_does_not_crash() -> None:
    assert not STUB.autostart.enable("/x").success
    assert STUB.autostart.disable().success and STUB.autostart.ensure_current("/x") is False


def test_neutral_functions_hand_the_executable_to_the_platform() -> None:
    fake = MagicMock()
    fake.autostart.enable.return_value = GuiAutostartResult(success=True)
    with patch.object(public, "current_platform", return_value=fake), patch.object(
        public, "_gui_autostart_executable", return_value="/opt/zapretgui/zapretgui"
    ):
        assert public.enable_gui_autostart(status_cb=print).success
        fake.autostart.enable.assert_called_once_with("/opt/zapretgui/zapretgui", status_cb=print)
        public.disable_gui_autostart()
        fake.autostart.disable.assert_called_once_with()


def test_migration_runs_only_when_the_setting_is_on() -> None:
    fake = MagicMock()
    fake.autostart.ensure_current.return_value = True
    with (
        patch.object(public, "current_platform", return_value=fake),
        patch.object(public, "_gui_autostart_executable", return_value="/opt/z"),
        patch("settings.store.get_gui_autostart_enabled", return_value=False),
    ):
        assert public.ensure_gui_autostart_migrated() is False
        fake.autostart.ensure_current.assert_not_called()
    with (
        patch.object(public, "current_platform", return_value=fake),
        patch.object(public, "_gui_autostart_executable", return_value="/opt/z"),
        patch("settings.store.get_gui_autostart_enabled", return_value=True),
    ):
        assert public.ensure_gui_autostart_migrated() is True
        fake.autostart.ensure_current.assert_called_once_with("/opt/z")


def test_migration_swallows_platform_failures() -> None:
    fake = MagicMock()
    fake.autostart.ensure_current.side_effect = RuntimeError("boom")
    with (
        patch.object(public, "current_platform", return_value=fake),
        patch("settings.store.get_gui_autostart_enabled", return_value=True),
    ):
        assert public.ensure_gui_autostart_migrated() is False
