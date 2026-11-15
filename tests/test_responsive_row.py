"""ResponsiveRow: две части рядом, а при нехватке ширины одна под другой, без наложения."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QWidget

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


def _box(width: int, height: int = 30) -> QWidget:
    widget = QWidget()
    widget.setFixedSize(width, height)
    return widget


class ResponsiveRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _row(self, **kwargs):
        from ui.widgets.responsive_row import ResponsiveRow

        lead, trail = _box(300), _box(200)
        row = ResponsiveRow(lead, trail, spacing=16, **kwargs)
        return row, lead, trail

    def test_wide_row_keeps_parts_side_by_side(self) -> None:
        row, lead, trail = self._row()
        row.resize(700, 100)
        row.show()
        self._app.processEvents()
        self.assertFalse(row.is_narrow)
        self.assertEqual(lead.geometry().top(), trail.geometry().top())
        self.assertGreaterEqual(trail.geometry().left(), lead.geometry().right() + 16)
        # Замыкающая часть прижата вправо.
        self.assertEqual(trail.geometry().right(), 699)

    def test_narrow_row_stacks_parts_without_overlap(self) -> None:
        row, lead, trail = self._row()
        row.resize(400, 100)
        row.show()
        self._app.processEvents()
        self.assertTrue(row.is_narrow)
        self.assertGreater(trail.geometry().top(), lead.geometry().bottom())
        self.assertFalse(lead.geometry().intersects(trail.geometry()))

    def test_row_returns_to_one_line_when_widened(self) -> None:
        row, lead, trail = self._row()
        row.show()
        row.resize(400, 100)
        self._app.processEvents()
        self.assertTrue(row.is_narrow)
        row.resize(800, 100)
        self._app.processEvents()
        self.assertFalse(row.is_narrow)
        self.assertEqual(lead.geometry().top(), trail.geometry().top())

    def test_growing_part_triggers_wrapping_without_a_resize(self) -> None:
        row, lead, trail = self._row()
        row.resize(560, 100)
        row.show()
        self._app.processEvents()
        self.assertFalse(row.is_narrow)
        trail.setFixedWidth(400)  # теперь 300 + 16 + 400 > 560
        self._app.processEvents()
        self.assertTrue(row.is_narrow)

    def test_lead_width_hint_is_used_for_the_decision(self) -> None:
        row, lead, trail = self._row(lead_width=lambda: 480)
        row.resize(600, 100)  # 300 + 16 + 200 влезло бы, а 480 + 16 + 200 нет
        row.show()
        self._app.processEvents()
        self.assertTrue(row.is_narrow)

    def test_filling_lead_uses_the_whole_width_in_both_modes(self) -> None:
        row, lead, trail = self._row(fill_lead=True, lead_width=lambda: 300)
        lead.setMinimumWidth(0)
        lead.setMaximumWidth(16777215)
        row.resize(700, 100)
        row.show()
        self._app.processEvents()
        self.assertFalse(row.is_narrow)
        self.assertGreaterEqual(lead.width(), 400)
        row.resize(400, 100)
        self._app.processEvents()
        self.assertTrue(row.is_narrow)
        self.assertEqual(lead.width(), 400)


if __name__ == "__main__":
    unittest.main()
