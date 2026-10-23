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

    def test_each_frame_repaints_only_the_strip_the_capsule_moves_through(self) -> None:
        # Слой прозрачный и лежит над всем списком: update() без области
        # перерисовывал под ним каждую плитку на каждом кадре.
        self._switch_active(1, 4)
        anim = self.motion._anim
        viewport_area = self.view.viewport().width() * self.view.viewport().height()
        updates: list[tuple] = []
        with mock.patch.object(self.motion._overlay, "update", side_effect=lambda *args: updates.append(args)):
            for share in (0.05, 0.15, 0.3, 0.45, 0.55, 0.7, 0.85, 0.95):
                anim.setCurrentTime(int(anim.duration() * share))

        self.assertEqual(len(updates), 8)
        for args in updates:
            self.assertEqual(len(args), 1, "update() без области перерисовывает весь список")
            strip = args[0]
            self.assertLess(strip.width(), 40)
            self.assertLess(strip.width() * strip.height(), viewport_area / 4)

    def test_strip_covers_everything_the_capsule_draws(self) -> None:
        self._switch_active(1, 4)
        anim = self.motion._anim
        for share in (0.1, 0.25, 0.4, 0.5, 0.65, 0.8):
            anim.setCurrentTime(int(anim.duration() * share))
            left, top, height, tail, direction, _alpha = self.motion._capsule_geometry()
            strip = self.motion._capsule_strip(self.motion._capsule_geometry())
            tail_top = top - tail if direction > 0 else top
            tail_bottom = top + height + tail if direction <= 0 else top + height
            self.assertLessEqual(strip.left(), left - 3)
            self.assertGreaterEqual(strip.right() + 1, left + 7)
            self.assertLessEqual(strip.top(), min(top - 3, tail_top))
            self.assertGreaterEqual(strip.bottom() + 1, max(top + height + 3, tail_bottom))

    def test_every_drawn_pixel_lies_inside_the_repainted_strip(self) -> None:
        # Проверка по настоящей картинке слоя, а не по тем же формулам: всё, что
        # нарисовано, обязано попасть в полоску, которую перерисовывает кадр.
        for old_row, new_row in ((1, 4), (4, 1)):
            self._switch_active(old_row, new_row)
            anim = self.motion._anim
            for share in (0.1, 0.25, 0.4, 0.5, 0.65, 0.8, 0.95):
                anim.setCurrentTime(int(anim.duration() * share))
                geometry = self.motion._capsule_geometry()
                # Картинка слоя — в его координатах, полоска — в координатах списка.
                strip = self.motion._capsule_strip(geometry).translated(-self.motion._overlay.pos())
                image = self.motion._overlay.grab().toImage()
                width, height = image.width(), image.height()
                alpha = bytes(image.constBits().asarray(image.sizeInBytes()))[3::4]
                drawn = [index for index, value in enumerate(alpha) if value]
                self.assertTrue(drawn)
                for index in (drawn[0], drawn[-1]):
                    self.assertTrue(strip.contains(index % width, index // width), (share, strip, index % width, index // width))
                xs = [index % width for index in drawn]
                self.assertGreaterEqual(min(xs), strip.left())
                self.assertLessEqual(max(xs), strip.right())
            anim.setCurrentTime(anim.duration())
            _drain()

    def test_overlay_covers_only_the_path_of_the_marker_not_the_whole_list(self) -> None:
        # Прозрачный слой при показе и скрытии перерисовывает всё, что под ним,
        # поэтому он по размеру пути капсулы.
        self._switch_active(1, 3)
        viewport = self.view.viewport().rect()
        area = self.motion._overlay.geometry()

        self.assertTrue(viewport.contains(area))
        self.assertLess(area.width(), viewport.width() // 2)
        old_row = self.view.visualRect(self.view.model().index(1, 0))
        new_row = self.view.visualRect(self.view.model().index(3, 0))
        self.assertLessEqual(area.top(), old_row.top())
        self.assertGreaterEqual(area.bottom(), new_row.bottom())

    def test_overlay_grows_when_the_list_is_scrolled_during_the_slide(self) -> None:
        self._switch_active(1, 3)
        anim = self.motion._anim
        anim.setCurrentTime(int(anim.duration() * 0.3))
        before = self.motion._overlay.geometry()
        # Строки уехали (список прокрутили): капсула вышла из слоя.
        with mock.patch.object(self.motion, "_row_rect", side_effect=lambda index: self.view.visualRect(
            self.view.model().index(index.row(), 0)).translated(0, 100)):
            anim.setCurrentTime(int(anim.duration() * 0.35))
        grown = self.motion._overlay.geometry()
        self.assertTrue(grown.contains(before))
        self.assertGreater(grown.bottom(), before.bottom())

    def test_small_overlay_draws_exactly_what_a_full_size_overlay_draws(self) -> None:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QColor, QImage

        def render() -> QImage:
            image = QImage(self.view.viewport().size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0, 0))
            overlay = self.motion._overlay
            overlay.render(image, QPoint(overlay.x(), overlay.y()))
            return image

        for old_row, new_row in ((1, 4), (4, 1)):
            self._switch_active(old_row, new_row)
            anim = self.motion._anim
            for share in (0.1, 0.3, 0.5, 0.7, 0.9):
                anim.setCurrentTime(int(anim.duration() * share))
                small = render()
                small_geometry = self.motion._overlay.geometry()
                self.motion._overlay.setGeometry(self.view.viewport().rect())
                full = render()
                self.motion._overlay.setGeometry(small_geometry)
                self.assertEqual(small, full, (old_row, new_row, share))
            anim.setCurrentTime(anim.duration())
            _drain()

    def test_old_row_is_not_repainted_after_its_highlight_has_faded(self) -> None:
        # Список рисует плитки по объемлющему прямоугольнику всех грязных мест:
        # далёкая старая строка заставляла перерисовывать всё между ней и новой.
        self._switch_active(1, 4)
        anim = self.motion._anim
        old_rect = self.view.visualRect(self.view.model().index(1, 0)).adjusted(0, -8, 0, 8)
        updates: list = []
        with mock.patch.object(self.view.viewport(), "update", side_effect=lambda *args: updates.append(args[0])):
            anim.setCurrentTime(int(anim.duration() * 0.3))
            before_fade_end = [rect for rect in updates if rect.intersects(old_rect)]
            updates.clear()
            # Первый кадр после угасания ещё рисует старую строку в конечном
            # виде; после него она до конца анимации не меняется.
            anim.setCurrentTime(int(anim.duration() * 0.45))
            final_frame = [rect for rect in updates if rect.intersects(old_rect)]
            updates.clear()
            for share in (0.6, 0.75, 0.9):
                anim.setCurrentTime(int(anim.duration() * share))

        self.assertTrue(before_fade_end, "пока подсветка гаснет, старую строку нужно обновлять")
        self.assertTrue(final_frame, "старая строка должна один раз перерисоваться уже погасшей")
        self.assertEqual([rect for rect in updates if rect.intersects(old_rect)], [])
        # Новая строка до конца прыжка значка обновляется.
        new_rect = self.view.visualRect(self.view.model().index(4, 0))
        self.assertTrue(any(rect.intersects(new_rect) for rect in updates))

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
