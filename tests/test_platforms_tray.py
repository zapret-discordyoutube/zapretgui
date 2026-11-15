"""The ``tray`` interface: factories per backend and the stand-in manager for desktops without a tray."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from platforms.linux import PLATFORM as LINUX
from platforms.no_tray import NoTrayManager
from platforms.stub import PLATFORM as STUB
from platforms.windows import PLATFORM as WINDOWS


def _make(backend, port=None):
    return backend.tray.create_manager(window_port=port, icon_path="/x/icon.ico", app_version="1.2", tray_feature=object())


def test_linux_and_stub_give_the_quiet_stand_in() -> None:
    for backend in (LINUX, STUB):
        manager = _make(backend)
        assert isinstance(manager, NoTrayManager)
        assert manager.icon_path == "/x/icon.ico" and manager.app_version == "1.2"


def test_stand_in_never_hides_the_window_and_every_call_is_safe() -> None:
    port = MagicMock()
    manager = _make(LINUX, port)
    assert manager.hide_to_tray(show_hint=True) is False  # no icon to bring the window back
    port.hide.assert_not_called()
    manager.apply_launch_status(phase="running", launch_method="winws2", preset_name="x")
    manager.show_notification("t", "m")
    manager.recreate_icon()
    manager.prewarm_menu()
    manager.hide_icon()
    manager.cleanup()


def test_stand_in_shows_the_window_and_forwards_exit() -> None:
    port = MagicMock()
    manager = _make(LINUX, port)
    manager.show_window()
    manager.toggle_window_visibility()
    assert port.show.call_count == 2
    manager.exit_only()
    port.request_exit.assert_called_with(stop_dpi=False)
    manager.exit_and_stop()
    port.request_exit.assert_called_with(stop_dpi=True)


def test_stand_in_survives_a_window_that_cannot_be_shown_and_a_missing_port() -> None:
    port = MagicMock()
    port.show.side_effect = RuntimeError("gone")
    _make(LINUX, port).show_window()
    _make(LINUX, None).show_window()
    _make(LINUX, None).exit_only()


def test_windows_factory_builds_the_native_manager_lazily() -> None:
    fake_class = MagicMock()
    with patch("platforms.windows.tray.SystemTrayManager", fake_class):
        manager = WINDOWS.tray.create_manager(window_port="port", icon_path="i.ico", app_version="9", tray_feature="feat")
    fake_class.assert_called_once_with("port", "i.ico", "9", tray_feature="feat")
    assert manager is fake_class.return_value


def test_the_tray_facade_takes_its_factory_from_the_platform() -> None:
    import inspect

    from app.feature_facades import tray as facade

    source = inspect.getsource(facade.TrayFeature._init_manager)
    assert "current_platform().tray.create_manager" in source
    assert "SystemTrayManager" not in source


def test_the_stand_in_notifies_through_notify_send_where_it_exists() -> None:
    import sys

    from platforms.no_tray import NoTrayManager

    if not sys.platform.startswith("linux"):
        return
    manager = NoTrayManager()
    with patch("shutil.which", return_value="/usr/bin/notify-send"), patch("subprocess.Popen") as popen:
        manager.show_notification("Заголовок", "Текст", 3000)
    command = popen.call_args.args[0]
    assert command[0] == "/usr/bin/notify-send" and "--expire-time=3000" in command and command[-2:] == ["Заголовок", "Текст"]
    with patch("shutil.which", return_value=None), patch("subprocess.Popen") as popen:
        manager.show_notification("a", "b")
    popen.assert_not_called()
    with patch("shutil.which", return_value="/usr/bin/notify-send"), patch("subprocess.Popen", side_effect=OSError("x")):
        manager.show_notification("a", "b")  # a missing service never breaks the program
