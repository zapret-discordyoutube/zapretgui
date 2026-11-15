"""The Linux tray manager with a fake QSystemTrayIcon (offscreen Qt has no system tray)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMenu

from platforms.linux import qt_tray
from platforms.linux.qt_tray import QtTrayManager


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeIcon(QObject):
    activated = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.tooltip = ""
        self.visible = False

    def setContextMenu(self, menu):  # noqa: N802
        self.calls.append(("menu", menu))

    def setIcon(self, icon):  # noqa: N802
        self.calls.append(("icon", icon.isNull()))

    def setToolTip(self, text):  # noqa: N802
        self.tooltip = text

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def showMessage(self, title, message, icon, msec):  # noqa: N802
        self.calls.append(("message", title, message, msec))

    def deleteLater(self):  # noqa: N802
        pass


def _port(visible=True):
    port = MagicMock()
    port.is_visible.return_value = visible
    port.ui_language.return_value = "ru"
    return port


def _make(qapp, port=None, feature=None):
    port = port or _port()
    feature = feature or MagicMock()
    manager = QtTrayManager(window_port=port, icon_path="", app_version="1", tray_feature=feature, icon_cls=FakeIcon)
    return manager, port, feature


def _texts(manager):
    return [action.text() for action in manager._menu.actions() if not action.isSeparator()]


def test_the_icon_is_shown_with_a_menu_and_a_painted_icon(qapp) -> None:
    manager, _port_, _feature = _make(qapp)
    assert manager._icon.visible
    assert ("menu", manager._menu) in manager._icon.calls
    assert ("icon", False) in manager._icon.calls  # the logo (even an empty one) is painted into a real image


def test_menu_texts_follow_the_state(qapp) -> None:
    manager, port, _feature = _make(qapp)
    assert _texts(manager) == ["Скрыть в трей", "Запустить Zapret", "Выход", "Выход и остановить"]
    port.is_visible.return_value = False
    manager.apply_launch_status(phase="running", launch_method="direct_zapret2", preset_name="P")
    assert _texts(manager)[:2] == ["Показать окно", "Остановить Zapret"]


def test_the_tooltip_names_the_state_and_the_preset(qapp) -> None:
    manager, _port_, _feature = _make(qapp)
    manager.apply_launch_status(phase="running", launch_method="direct_zapret2", preset_name="Мой пресет")
    assert "Мой пресет" in manager._icon.tooltip


def test_the_status_dot_is_drawn_for_a_running_engine_only(qapp) -> None:
    from ui.tray_status import render_status_icon_image, tray_icon_dot_color

    base = QImage(32, 32, QImage.Format.Format_ARGB32)
    base.fill(QColor("#ffffff"))
    with_dot = render_status_icon_image(base, tray_icon_dot_color("running"), 32)
    without = render_status_icon_image(base, None, 32)
    assert with_dot != without
    assert tray_icon_dot_color("running") is not None
    assert tray_icon_dot_color("failed") is None  # an error is shown in the menu and notifications, not as a red dot


def test_the_menu_items_call_the_program(qapp) -> None:
    manager, port, feature = _make(qapp)
    show, start_stop, exit_only, exit_stop = [a for a in manager._menu.actions() if not a.isSeparator()]
    start_stop.trigger()
    feature.toggle_dpi.assert_called_once_with()
    exit_only.trigger()
    port.request_exit.assert_called_with(stop_dpi=False)
    exit_stop.trigger()
    port.request_exit.assert_called_with(stop_dpi=True)
    show.trigger()  # the window is visible: the item hides it
    port.hide.assert_called_once_with()


def test_a_click_on_the_icon_toggles_the_window(qapp) -> None:
    from PyQt6.QtWidgets import QSystemTrayIcon

    manager, port, _feature = _make(qapp, _port(visible=True))
    manager._icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    port.hide.assert_called_once_with()
    port.is_visible.return_value = False
    manager._icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    port.show.assert_called_once_with()
    manager._icon.activated.emit(QSystemTrayIcon.ActivationReason.Context)  # right click opens the menu: no toggle
    assert port.show.call_count == 1 and port.hide.call_count == 1


def test_hiding_shows_the_hint_once(qapp) -> None:
    manager, port, _feature = _make(qapp)
    assert manager.hide_to_tray(show_hint=True) is True
    assert manager.hide_to_tray(show_hint=True) is True
    messages = [call for call in manager._icon.calls if call[0] == "message"]
    assert len(messages) == 1
    port.persist_geometry.assert_called()


def test_a_window_that_cannot_be_hidden_reports_failure(qapp) -> None:
    port = _port()
    port.hide.side_effect = RuntimeError("gone")
    manager, _p, _f = _make(qapp, port)
    assert manager.hide_to_tray() is False


def test_cleanup_hides_the_icon(qapp) -> None:
    manager, _port_, _feature = _make(qapp)
    manager.cleanup()
    assert manager._icon.visible is False


def test_the_factory_gives_the_real_tray_only_where_the_desktop_has_one(qapp) -> None:
    from platforms.linux.tray_factory import GNOME_HINT, LinuxTray
    from platforms.no_tray import NoTrayManager

    kwargs = dict(window_port=_port(), icon_path="", app_version="1", tray_feature=MagicMock())
    with patch.object(qt_tray, "tray_available", return_value=False):
        assert isinstance(LinuxTray().create_manager(**kwargs), NoTrayManager)
    assert "AppIndicator" in GNOME_HINT
    with patch.object(qt_tray, "tray_available", return_value=True), patch.object(qt_tray, "QtTrayManager") as real:
        assert LinuxTray().create_manager(**kwargs) is real.return_value


def test_the_shared_tray_helpers_are_still_importable_from_the_windows_module() -> None:
    from platforms.windows import tray

    assert callable(tray.render_status_icon_image) and callable(tray.build_tray_tooltip) and callable(tray.tray_icon_dot_color)
