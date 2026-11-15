"""The ``desktop`` interface: safe answers on Linux and stub, and the neutral window code that calls it."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from platforms.linux import PLATFORM as LINUX
from platforms.stub import PLATFORM as STUB
from platforms.windows import PLATFORM as WINDOWS


def test_every_backend_has_a_desktop() -> None:
    for backend in (WINDOWS, LINUX, STUB):
        assert backend.desktop is not None


def test_linux_and_stub_have_nothing_to_hook_into_yet() -> None:
    for backend in (LINUX, STUB):
        desktop = backend.desktop
        window = MagicMock()
        assert desktop.user_idle_ms() is None
        assert desktop.uses_qt_file_drop() is True  # Qt delivers drops itself
        assert desktop.bind_window(window) is False
        assert desktop.dropped_file_paths(0) is None
        assert desktop.create_drag_hover_detector(window, on_hover_start=print, on_hover_end=print) is None
        assert desktop.handle_native_event(window, 0) is False
        assert desktop.cloak_until_painted(window) is None
        assert desktop.install_theme_watcher(MagicMock(), window) is None


def test_windows_does_not_use_qt_file_drop() -> None:
    assert WINDOWS.desktop.uses_qt_file_drop() is False


def test_windows_ignores_an_unreadable_native_message() -> None:
    assert WINDOWS.desktop.handle_native_event(MagicMock(), 0) is False
    assert WINDOWS.desktop.handle_native_event(MagicMock(), None) is False


def test_idle_time_goes_through_the_platform_and_never_raises() -> None:
    from ui import user_idle

    fake = MagicMock()
    fake.desktop.user_idle_ms.return_value = 4321
    with patch("platforms.current", return_value=fake):
        assert user_idle.user_idle_ms() == 4321
    fake.desktop.user_idle_ms.side_effect = RuntimeError("boom")
    with patch("platforms.current", return_value=fake):
        assert user_idle.user_idle_ms() is None


def test_window_native_event_is_consumed_only_when_the_platform_says_so() -> None:
    from main import window_lifecycle

    class Base:
        def nativeEvent(self, event_type, message):  # noqa: N802
            return ("base", message)

    class Window(window_lifecycle.WindowLifecycleMixin, Base):
        pass

    fake = MagicMock()
    window = Window.__new__(Window)
    with patch.object(window_lifecycle, "current_platform", return_value=fake):
        fake.desktop.handle_native_event.return_value = True
        assert window.nativeEvent(b"t", 7) == (True, 0)
        fake.desktop.handle_native_event.return_value = False
        assert window.nativeEvent(b"t", 7) == ("base", 7)
    fake.desktop.handle_native_event.assert_called_with(window, 7)


def test_minimize_request_is_neutral_and_needs_the_tray_mode() -> None:
    from main.window_native_commands import handle_minimize_request

    window = MagicMock()
    window.close_to_tray.return_value = True
    assert handle_minimize_request(window, minimize_to_tray_enabled=None) is False
    assert handle_minimize_request(window, minimize_to_tray_enabled=lambda: False) is False
    window.close_to_tray.assert_not_called()
    assert handle_minimize_request(window, minimize_to_tray_enabled=lambda: True) is True
    window.close_to_tray.side_effect = RuntimeError("boom")
    assert handle_minimize_request(window, minimize_to_tray_enabled=lambda: True) is False
