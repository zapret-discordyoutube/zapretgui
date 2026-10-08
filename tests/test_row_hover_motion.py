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

    def test_hover_answers_on_the_first_frames_and_settles_fast(self) -> None:
        self._move_to_row(2)
        _wait(0.03)

        level = self.motion.hover_level(self._index(2))
        # Отклик виден сразу: за пару кадров пройдена заметная часть пути.
        self.assertGreater(level, 0.4)
        self.assertEqual(self.motion.hover_level(self._index(3)), 0.0)

        _wait(0.2)
        self.assertEqual(self.motion.hover_level(self._index(2)), 1.0)
        self.assertFalse(self.motion._timer.isActive())

    def test_level_moves_without_jumps_when_mouse_turns_back(self) -> None:
        self.motion._timer.stop()
        self.motion._hover_row = 2
        self.motion._levels = {2: 0.6}
        self.motion._last_tick = time.monotonic() - 0.008
        self.motion._tick()
        rising = self.motion.hover_level(self._index(2))
        self.motion._hover_row = -1
        self.motion._last_tick = time.monotonic() - 0.008
        self.motion._tick()
        falling = self.motion.hover_level(self._index(2))

        self.assertGreater(rising, 0.6)
        self.assertLess(falling, rising)
        # Разворот начинается с того же места, а не с нуля или единицы.
        self.assertGreater(falling, 0.5)

    def test_timer_is_precise(self) -> None:
        self.assertEqual(self.motion._timer.timerType(), Qt.TimerType.PreciseTimer)

    def test_leaving_row_fades_out(self) -> None:
        self._move_to_row(2)
        _wait(0.25)
        QApplication.sendEvent(self.view.viewport(), QEvent(QEvent.Type.Leave))
        _wait(0.03)

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
        self.assertFalse(self.motion._timer.isActive())

    def test_live_animations_off_keeps_instant_hover(self) -> None:
        with mock.patch.object(motion_module, "are_live_animations_enabled", return_value=False):
            self._move_to_row(2)
            _wait(0.06)
            self.assertIsNone(self.motion.hover_level(self._index(2)))
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
        finally:
            painter.end()

        self.assertEqual(instant, hover)
        self.assertEqual(pressed, hover)
        if idle != hover:
            self.assertNotIn(half, (idle, hover))



if __name__ == "__main__":
    unittest.main()
