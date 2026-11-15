"""WrapLayout: ряд кнопок переносится на вторую строку, скрытые кнопки места не занимают."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QWidget

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


def _box(parent: QWidget, width: int, height: int = 30) -> QWidget:
    widget = QWidget(parent)
    widget.setFixedSize(width, height)
    return widget


class WrapLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _row(self, width: int, sizes=(150, 160, 320)):
        from ui.widgets.wrap_layout import WrapLayout

        host = QWidget()
        layout = WrapLayout(host, horizontal_spacing=10, vertical_spacing=8)
        boxes = [_box(host, size) for size in sizes]
        for box in boxes:
            layout.addWidget(box)
        host.resize(width, 200)
        host.show()
        self._app.processEvents()
        return host, layout, boxes

    def test_everything_fits_in_one_line_when_wide_enough(self) -> None:
        _, layout, boxes = self._row(800)
        self.assertEqual({box.y() for box in boxes}, {0})
        self.assertEqual(boxes[1].x(), 160)
        self.assertEqual(layout.heightForWidth(800), 30)

    def test_last_item_wraps_to_a_second_line_without_overlap(self) -> None:
        _, layout, boxes = self._row(500)
        self.assertEqual(boxes[0].y(), 0)
        self.assertEqual(boxes[1].y(), 0)
        self.assertEqual(boxes[2].y(), 38)
        self.assertEqual(boxes[2].x(), 0)
        self.assertEqual(layout.heightForWidth(500), 68)
        for index, first in enumerate(boxes):
            for second in boxes[index + 1:]:
                self.assertFalse(first.geometry().intersects(second.geometry()))

    def test_hidden_items_do_not_take_space(self) -> None:
        host, layout, boxes = self._row(500)
        boxes[0].hide()
        layout.invalidate()
        self._app.processEvents()
        self.assertEqual(boxes[1].x(), 0)
        self.assertEqual({box.y() for box in boxes if not box.isHidden()}, {0})
        boxes[0].show()
        layout.invalidate()
        self._app.processEvents()
        self.assertEqual(boxes[2].y(), 38)

    def test_insert_widget_and_add_stretch_work_like_a_box_layout(self) -> None:
        from ui.widgets.wrap_layout import WrapLayout

        host = QWidget()
        layout = WrapLayout(host)
        first, second, inserted = _box(host, 50), _box(host, 50), _box(host, 50)
        layout.addWidget(first)
        layout.addWidget(second)
        layout.addStretch(1)
        layout.insertWidget(0, inserted)
        layout.insertWidget(99, _box(host, 50))
        self.assertEqual(layout.count(), 4)
        self.assertIs(layout.itemAt(0).widget(), inserted)
        self.assertIs(layout.itemAt(1).widget(), first)

    def test_minimum_width_is_the_widest_item_so_the_page_may_shrink(self) -> None:
        _, layout, _ = self._row(800)
        self.assertEqual(layout.minimumSize().width(), 320)


if __name__ == "__main__":
    unittest.main()
