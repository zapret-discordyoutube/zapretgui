from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QStyleOptionViewItem

from ui.presets_menu.delegate import PresetListDelegate
from ui.presets_menu.model import PresetListModel
from ui.presets_menu.view import LinkedWheelListView
from ui.widgets.stable_center import center_point, center_x, center_y


class StableCenterTests(unittest.TestCase):
    def test_it_equals_qrect_center_for_every_rectangle_that_is_not_above_or_left_of_the_origin(self) -> None:
        for left in (0, 1, 7, 100):
            for top in (0, 1, 2, 13, 250):
                for width in (1, 2, 3, 18, 209, 840):
                    for height in (1, 2, 3, 24, 28, 31, 32):
                        rect = QRect(left, top, width, height)
                        self.assertEqual((center_x(rect), center_y(rect)), (rect.center().x(), rect.center().y()), rect)
                        self.assertEqual(center_point(rect), rect.center())

    def test_it_does_not_depend_on_where_the_rectangle_is(self) -> None:
        # У QRect.center() для отрицательного верха центр выходит на точку ниже.
        offsets = {center_y(QRect(0, top, 10, 28)) - top for top in range(-60, 60)}
        self.assertEqual(offsets, {13})
        self.assertNotEqual({QRect(0, top, 10, 28).center().y() - top for top in range(-60, 60)}, {13})

    def test_the_centre_is_inside_the_rectangle(self) -> None:
        for top in (-40, -17, -1, 0, 9):
            rect = QRect(-5, top, 21, 28)
            self.assertTrue(rect.contains(center_point(rect)))


class TileAtTheTopEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.view = LinkedWheelListView()
        self.addCleanup(self.view.deleteLater)
        self.model = PresetListModel(self.view)
        rows = [{"kind": "folder", "folder_key": "a", "name": "Группа", "is_collapsed": False, "count": 3}]
        rows += [{"kind": "preset", "name": f"general ALT{n} (game filter) gjpqy", "file_name": f"p{n}.txt", "folder_key": "a", "depth": 1,
                  "icon_color": "#e0a030", "is_pinned": n == 1, "rating": 3 if n == 2 else 0} for n in range(3)]
        self.model.set_rows(rows)
        self.view.setModel(self.model)
        self.delegate = PresetListDelegate(self.view)
        self.view.setItemDelegate(self.delegate)

    def _paint(self, row: int, top: int) -> QImage:
        image = QImage(260, 140, QImage.Format.Format_RGB32)
        image.fill(QColor(31, 41, 59))
        painter = QPainter(image)
        option = QStyleOptionViewItem()
        option.rect = QRect(5, top, 209, 32)
        self.delegate.paint(painter, option, self.model.index(row, 0))
        painter.end()
        return image

    def test_a_tile_looks_the_same_whether_or_not_its_top_is_above_the_list(self) -> None:
        # Раньше значок и имя плитки с отрицательным верхом рисовались на пиксель
        # ниже и «прыгали» при прокрутке, когда верх пересекал верхний край.
        for row in (1, 2, 3):
            full = self._paint(row, 60)
            for top in (-19, -17, -13, -10, -7, -4, -1):
                cut = self._paint(row, top)
                shift = 60 - top
                for y in range(0, 140 - shift):
                    for x in range(260):
                        self.assertEqual(cut.pixel(x, y), full.pixel(x, y + shift), (row, top, x, y))

    def test_the_folder_header_looks_the_same_at_any_position_too(self) -> None:
        full = self._paint(0, 60)
        for top in (-23, -14, -9, -3):
            cut = self._paint(0, top)
            shift = 60 - top
            for y in range(0, 140 - shift):
                for x in range(260):
                    self.assertEqual(cut.pixel(x, y), full.pixel(x, y + shift), (top, x, y))


if __name__ == "__main__":
    unittest.main()
