from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem

import ui.widgets.active_row_motion as motion_module
from ui.widgets.active_row_motion import active_row_motion, attach_active_row_motion


ACTIVE_ROLE = int(Qt.ItemDataRole.UserRole) + 4


def _drain() -> None:
    for _ in range(3):
        QApplication.processEvents()


class ActiveRowMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        patcher = mock.patch.object(motion_module, "are_live_animations_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.view = QListWidget()
        self.addCleanup(self.view.deleteLater)
        for row in range(6):
            item = QListWidgetItem(f"row {row}")
            item.setData(ACTIVE_ROLE, row == 1)
            item.setSizeHint(QSize(0, 31))
            self.view.addItem(item)
        self.motion = attach_active_row_motion(self.view, ACTIVE_ROLE)
        self.view.resize(240, 240)
        self.view.show()
        _drain()

    def _switch_active(self, old_row: int, new_row: int) -> None:
        self.view.item(old_row).setData(ACTIVE_ROLE, False)
        self.view.item(new_row).setData(ACTIVE_ROLE, True)
        _drain()

    def test_attach_is_idempotent(self) -> None:
        self.assertIs(attach_active_row_motion(self.view, ACTIVE_ROLE), self.motion)
        self.assertIs(active_row_motion(self.view), self.motion)

    def test_switching_active_row_slides_marker_then_bounces_icon(self) -> None:
        self._switch_active(1, 4)

        self.assertTrue(self.motion.is_running())
        new_index = self.view.model().index(4, 0)
        old_index = self.view.model().index(1, 0)
        # Пока полоска едет, у новой строки своя полоска спрятана.
        self.assertTrue(self.motion.hides_static_marker(new_index))
        self.assertFalse(self.motion.hides_static_marker(old_index))
        self.assertEqual(self.motion.icon_offset(new_index), 0.0)

        anim = self.motion._anim
        anim.setCurrentTime(int(anim.duration() * 0.7))
        # На приземлении капсула пружинит сама, своя полоска строки ещё скрыта.
        self.assertTrue(self.motion.hides_static_marker(new_index))
        self.assertLess(self.motion.icon_offset(new_index), 0.0)

        anim.setCurrentTime(anim.duration())
        self.assertFalse(self.motion.is_running())
        self.assertEqual(self.motion.icon_offset(new_index), 0.0)
        self.assertFalse(self.motion._overlay.isVisible())

    def test_new_row_fills_while_old_row_fades(self) -> None:
        self._switch_active(1, 4)
        new_index = self.view.model().index(4, 0)
        old_index = self.view.model().index(1, 0)
        other_index = self.view.model().index(2, 0)
        anim = self.motion._anim

        self.assertEqual(self.motion.row_reveal(new_index), 0.0)
        self.assertEqual(self.motion.row_residual(old_index), 1.0)
        self.assertIsNone(self.motion.row_reveal(other_index))
        self.assertEqual(self.motion.row_residual(other_index), 0.0)

        anim.setCurrentTime(int(anim.duration() * 0.3))
        middle_reveal = self.motion.row_reveal(new_index)
        self.assertGreater(middle_reveal, 0.0)
        self.assertLess(self.motion.row_residual(old_index), 1.0)

        anim.setCurrentTime(int(anim.duration() * 0.6))
        self.assertEqual(self.motion.row_reveal(new_index), 1.0)
        self.assertEqual(self.motion.row_residual(old_index), 0.0)

        anim.setCurrentTime(anim.duration())
        self.assertIsNone(self.motion.row_reveal(new_index))
        self.assertEqual(self.motion.row_residual(old_index), 0.0)

    def test_rebuilding_the_list_does_not_animate(self) -> None:
        self.view.clear()
        for row in range(6):
            item = QListWidgetItem(f"row {row}")
            item.setData(ACTIVE_ROLE, row == 3)
            self.view.addItem(item)
        _drain()

        self.assertFalse(self.motion.is_running())
        self.assertEqual(self.motion._active.row(), 3)

    def test_other_roles_do_not_trigger_motion(self) -> None:
        self.view.item(2).setData(int(Qt.ItemDataRole.UserRole) + 7, "status")
        _drain()
        self.assertFalse(self.motion.is_running())

    def test_no_motion_when_live_animations_are_off(self) -> None:
        with mock.patch.object(motion_module, "are_live_animations_enabled", return_value=False):
            self._switch_active(1, 4)
        self.assertFalse(self.motion.is_running())
        self.assertEqual(self.motion._active.row(), 4)

    def test_no_motion_while_list_is_hidden(self) -> None:
        self.view.hide()
        self._switch_active(1, 4)
        self.assertFalse(self.motion.is_running())
        self.assertEqual(self.motion._active.row(), 4)


if __name__ == "__main__":
    unittest.main()
