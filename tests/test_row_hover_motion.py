from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPainter
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem

import ui.widgets.row_hover_motion as motion_module
from ui.widgets.hover_row import paint_profile_hover_row
from ui.widgets.row_hover_motion import attach_row_hover_motion, row_hover_motion


def _wait(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()


class RowHoverMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(motion_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = QListWidget()
        self.addCleanup(self.view.deleteLater)
        for row in range(5):
            item = QListWidgetItem(f"row {row}")
            item.setSizeHint(QSize(0, 40))
            item.setData(Qt.ItemDataRole.UserRole, "folder" if row == 0 else "preset")
            self.view.addItem(item)
        self.view.resize(260, 260)
        self.view.show()
        QApplication.processEvents()
        self.motion = attach_row_hover_motion(
            self.view,
            row_filter=lambda index: index.data(Qt.ItemDataRole.UserRole) == "preset",
        )

    def _move_to_row(self, row: int) -> None:
        center = self.view.visualRect(self.view.model().index(row, 0)).center()
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(center),
            QPointF(self.view.viewport().mapToGlobal(center)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.view.viewport(), event)

    def _index(self, row: int):
        return self.view.model().index(row, 0)

    def test_attach_is_idempotent(self) -> None:
        self.assertIs(attach_row_hover_motion(self.view), self.motion)
        self.assertIs(row_hover_motion(self.view), self.motion)

    def test_hover_fades_in_tilts_icon_and_runs_sheen_after_dwell_then_settles(self) -> None:
        self._move_to_row(2)
        _wait(0.06)

        level = self.motion.hover_level(self._index(2))
        self.assertGreater(level, 0.0)
        self.assertLess(level, 1.0)
        angle = self.motion.icon_angle(self._index(2))
        self.assertGreater(angle, 0.3)
        self.assertLessEqual(angle, motion_module.TILT_PEAK_DEG)
        self.assertGreater(self.motion.icon_scale(self._index(2)), 1.0)
        # Блик ждёт, пока мышь задержится на строке.
        self.assertIsNone(self.motion.sheen_progress(self._index(2)))
        self.assertEqual(self.motion.hover_level(self._index(3)), 0.0)

        _wait(0.2)
        self.assertIsNotNone(self.motion.sheen_progress(self._index(2)))

        _wait(0.9)
        self.assertEqual(self.motion.hover_level(self._index(2)), 1.0)
        self.assertEqual(self.motion.icon_angle(self._index(2)), 0.0)
        self.assertEqual(self.motion.icon_scale(self._index(2)), 1.0)
        self.assertIsNone(self.motion.sheen_progress(self._index(2)))
        self.assertFalse(self.motion._timer.isActive())

    def test_quick_pass_over_rows_does_not_flash_sheen(self) -> None:
        for row in (1, 2, 3, 4):
            self._move_to_row(row)
            _wait(0.03)
        _wait(0.3)
        for row in (1, 2, 3):
            self.assertIsNone(self.motion.sheen_progress(self._index(row)))
        self.assertIsNotNone(self.motion.sheen_progress(self._index(4)))

    def test_sheen_is_not_repeated_right_away_on_the_same_row(self) -> None:
        self._move_to_row(2)
        _wait(1.0)
        self._move_to_row(3)
        _wait(0.02)
        self._move_to_row(2)
        _wait(0.25)
        self.assertIsNone(self.motion.sheen_progress(self._index(2)))

    def test_icon_is_drawn_smoothly_while_moving(self) -> None:
        painter = mock.Mock()
        calls = []
        motion_module.paint_rotated(painter, QRect(0, 0, 14, 14), 5.0, lambda: calls.append(True), scale=1.05)
        hints = [call.args for call in painter.setRenderHint.call_args_list]
        self.assertIn((QPainter.RenderHint.SmoothPixmapTransform, True), hints)
        painter.rotate.assert_called_once_with(5.0)
        painter.scale.assert_called_once_with(1.05, 1.05)
        self.assertEqual(calls, [True])

        still = mock.Mock()
        motion_module.paint_rotated(still, QRect(0, 0, 14, 14), 0.0, lambda: calls.append(True))
        still.rotate.assert_not_called()

    def test_leaving_row_fades_out(self) -> None:
        self._move_to_row(2)
        _wait(0.7)
        QApplication.sendEvent(self.view.viewport(), QEvent(QEvent.Type.Leave))
        _wait(0.08)

        level = self.motion.hover_level(self._index(2))
        self.assertGreater(level, 0.0)
        self.assertLess(level, 1.0)
        _wait(0.4)
        self.assertEqual(self.motion.hover_level(self._index(2)), 0.0)
        self.assertFalse(self.motion._timer.isActive())

    def test_filtered_rows_do_not_animate(self) -> None:
        self._move_to_row(0)
        _wait(0.06)

        self.assertEqual(self.motion.hover_level(self._index(0)), 0.0)
        self.assertEqual(self.motion.icon_angle(self._index(0)), 0.0)
        self.assertFalse(self.motion._timer.isActive())

    def test_live_animations_off_keeps_instant_hover(self) -> None:
        with mock.patch.object(motion_module, "are_live_animations_enabled", return_value=False):
            self._move_to_row(2)
            _wait(0.06)
            self.assertIsNone(self.motion.hover_level(self._index(2)))
            self.assertEqual(self.motion.icon_angle(self._index(2)), 0.0)
            self.assertFalse(self.motion._timer.isActive())

    def test_hover_level_blends_row_background(self) -> None:
        image = QImage(200, 40, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        try:
            rect = QRect(0, 0, 200, 40)
            idle = paint_profile_hover_row(painter, rect, hovered=False).background
            hover = paint_profile_hover_row(painter, rect, hovered=True).background
            instant = paint_profile_hover_row(painter, rect, hovered=True, hover_level=None).background
            half = paint_profile_hover_row(painter, rect, hovered=True, hover_level=0.5).background
            pressed = paint_profile_hover_row(painter, rect, pressed=True, hover_level=0.0).background
            paint_profile_hover_row(painter, rect, hovered=True, hover_level=1.0, sheen=0.4)
        finally:
            painter.end()

        self.assertEqual(instant, hover)
        self.assertEqual(pressed, hover)
        if idle != hover:
            self.assertNotIn(half, (idle, hover))

    def test_preset_profile_and_strategy_delegates_attach_hover_motion(self) -> None:
        from PyQt6.QtWidgets import QListView

        from profile.ui.profile_list_delegate import ProfileListDelegate
        from profile.ui.profile_strategy_list_widget import ProfileStrategyListDelegate
        from ui.presets_menu.delegate import PresetListDelegate

        for delegate_cls in (PresetListDelegate, ProfileListDelegate, ProfileStrategyListDelegate):
            with self.subTest(delegate=delegate_cls.__name__):
                view = QListView()
                self.addCleanup(view.deleteLater)
                delegate_cls(view)
                self.assertIsNotNone(row_hover_motion(view))


if __name__ == "__main__":
    unittest.main()
