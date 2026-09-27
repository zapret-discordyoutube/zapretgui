from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QIcon, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.widgets import spinning_logo as spinning_logo_module
from ui.widgets.spinning_logo import SPIN_TURN_DEGREES, SpinningLogo
from updater.ui import sync_icon as sync_icon_module
from updater.ui.plans import build_update_status_transition_plan
from updater.ui.sync_icon import ICON_MODE_CHECKING, ICON_MODE_ERROR, ICON_MODE_IDLE, UpdateSyncIcon


def _icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.cyan)
    return QIcon(pixmap)


def _click(widget: QWidget) -> None:
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    for event_type in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        event = QMouseEvent(
            event_type,
            pos,
            widget.mapToGlobal(pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton if event_type == QMouseEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(widget, event)


class _Host(QWidget):
    def __init__(self, child: QWidget) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(child)


class SpinningLogoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _make_logo(self) -> SpinningLogo:
        logo = SpinningLogo(_icon(), box_size=20)
        host = _Host(logo)
        self.addCleanup(host.deleteLater)
        host.show()
        return logo

    def test_click_starts_one_full_turn(self) -> None:
        logo = self._make_logo()
        clicks: list[bool] = []
        logo.clicked.connect(lambda: clicks.append(True))

        with mock.patch.object(spinning_logo_module, "are_live_animations_enabled", return_value=True):
            _click(logo)

        self.assertEqual(clicks, [True])
        self.assertTrue(logo.is_spinning())
        self.assertEqual(logo._spin.endValue(), SPIN_TURN_DEGREES)

    def test_repeated_clicks_add_turns(self) -> None:
        logo = self._make_logo()

        with mock.patch.object(spinning_logo_module, "are_live_animations_enabled", return_value=True):
            _click(logo)
            _click(logo)
            _click(logo)

        self.assertEqual(logo._spin.endValue(), SPIN_TURN_DEGREES * 3)

    def test_no_spin_when_animations_are_disabled(self) -> None:
        logo = self._make_logo()

        with mock.patch.object(spinning_logo_module, "are_live_animations_enabled", return_value=False):
            _click(logo)

        self.assertFalse(logo.is_spinning())
        self.assertEqual(logo.angle(), 0.0)

    def test_press_is_accepted_so_title_bar_does_not_drag_window(self) -> None:
        logo = self._make_logo()
        pos = QPointF(5, 5)
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            pos,
            logo.mapToGlobal(pos),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        event.ignore()
        QApplication.sendEvent(logo, event)
        self.assertTrue(event.isAccepted())

    def test_keyboard_enter_spins(self) -> None:
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent

        logo = self._make_logo()
        with mock.patch.object(spinning_logo_module, "are_live_animations_enabled", return_value=True):
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(logo, event)
        self.assertTrue(event.isAccepted())
        self.assertTrue(logo.is_spinning())

    def test_hiding_stops_spin_and_resets_angle(self) -> None:
        logo = self._make_logo()
        with mock.patch.object(spinning_logo_module, "are_live_animations_enabled", return_value=True):
            logo.spin()
        logo._on_spin_value(123.0)

        logo.hide()

        self.assertFalse(logo.is_spinning())
        self.assertEqual(logo.angle(), 0.0)


class UpdateSyncIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _drain(self) -> None:
        for _ in range(3):
            QApplication.processEvents()

    def test_checking_spins_only_after_widget_is_shown(self) -> None:
        icon = UpdateSyncIcon(size=40)
        host = _Host(icon)
        self.addCleanup(host.deleteLater)

        with mock.patch.object(sync_icon_module, "are_live_animations_enabled", return_value=True):
            icon.set_mode(ICON_MODE_CHECKING)
            self._drain()
            self.assertFalse(icon.is_spinning())

            host.show()
            self._drain()
            self.assertTrue(icon.is_spinning())

    def test_checking_stays_static_when_animations_are_disabled(self) -> None:
        icon = UpdateSyncIcon(size=40)
        host = _Host(icon)
        self.addCleanup(host.deleteLater)
        host.show()

        with mock.patch.object(sync_icon_module, "are_live_animations_enabled", return_value=False):
            icon.set_mode(ICON_MODE_CHECKING)
            self._drain()

        self.assertEqual(icon.mode(), ICON_MODE_CHECKING)
        self.assertFalse(icon.is_spinning())

    def test_stop_settles_to_upright_pose(self) -> None:
        icon = UpdateSyncIcon(size=40)
        host = _Host(icon)
        self.addCleanup(host.deleteLater)
        host.show()

        with mock.patch.object(sync_icon_module, "are_live_animations_enabled", return_value=True):
            icon.set_mode(ICON_MODE_CHECKING)
            self._drain()
        icon._on_spin_value(0.37)

        icon.set_mode(ICON_MODE_IDLE)

        self.assertFalse(icon.is_spinning())
        self.assertEqual(icon._settle_to_angle % 180.0, 0.0)
        self.assertGreater(icon._settle_to_angle, icon._settle_from_angle)
        icon._settle.setCurrentTime(icon._settle.duration())
        self.assertEqual(icon.angle(), 0.0)

    def test_error_resets_pose_immediately(self) -> None:
        icon = UpdateSyncIcon(size=40)
        icon._on_spin_value(0.4)

        icon.set_mode(ICON_MODE_ERROR)

        self.assertEqual(icon.mode(), ICON_MODE_ERROR)
        self.assertEqual(icon.angle(), 0.0)
        icon.deleteLater()

    def test_checking_plan_asks_icon_to_spin(self) -> None:
        plan = build_update_status_transition_plan(target_state="checking", language="ru")
        self.assertEqual(plan.icon_mode, "checking")


if __name__ == "__main__":
    unittest.main()
