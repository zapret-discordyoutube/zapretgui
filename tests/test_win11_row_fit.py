from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.widgets.win11_controls import Win11ComboRow, Win11ToggleRow

LONG_DESC = "После запуска ZapretGUI автоматически запускать текущий DPI-режим и ещё немного текста"


class Win11RowFitTests(unittest.TestCase):
    """В узком окне текст строки уступает место переключателю и списку, а не вылезает за край."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _host(self, row, width: int) -> QWidget:
        host = QWidget()
        QVBoxLayout(host).addWidget(row)
        host.resize(width, 120)
        self.addCleanup(host.deleteLater)
        host.show()
        QApplication.processEvents()
        return host

    def test_toggle_row_elides_long_text_and_keeps_it_in_tooltip(self) -> None:
        row = Win11ToggleRow("fa5s.bolt", "Автозапуск DPI после старта программы", LONG_DESC)
        host = self._host(row, 420)
        desc = row._desc_label

        self.assertTrue(desc.text().endswith("…"))
        self.assertEqual(desc.toolTip(), LONG_DESC)
        self.assertLessEqual(desc.x() + desc.fontMetrics().horizontalAdvance(desc.text()), row._switch_button.x())

        host.resize(1200, 120)
        QApplication.processEvents()
        self.assertEqual(desc.text(), LONG_DESC)
        self.assertEqual(desc.toolTip(), "")

    def test_retranslated_text_is_fitted_from_the_full_text(self) -> None:
        row = Win11ToggleRow("fa5s.bolt", "Заголовок", LONG_DESC)
        host = self._host(row, 420)
        row.set_texts("Autostart DPI", "Short text")

        self.assertEqual(row._desc_label.text(), "Short text")
        self.assertEqual(row._full_description, "Short text")
        host.resize(1200, 120)
        QApplication.processEvents()
        self.assertEqual(row._title_label.text(), "Autostart DPI")

    def test_combo_row_shrinks_its_list_and_text_fits_beside_it(self) -> None:
        row = Win11ComboRow(
            "fa5s.window-minimize",
            "Поведение окна и трея",
            "Выберите, когда ZapretGUI будет скрывать окно в системный трей",
            items=[("Свернуть и крестик скрывают в трей", "a"), ("Не скрывать в трей", "b")],
        )
        row.set_combo_width_range(170, 320)
        host = self._host(row, 1200)
        self.assertEqual(row.combo.width(), 320)
        self.assertFalse(row._desc_label.text().endswith("…"))

        host.resize(470, 120)
        QApplication.processEvents()
        self.assertLess(row.combo.width(), 320)
        self.assertGreaterEqual(row.combo.width(), 170)
        self.assertLessEqual(row.combo.geometry().right(), row.width())
        desc = row._desc_label
        self.assertTrue(desc.text().endswith("…"))
        self.assertLessEqual(desc.x() + desc.fontMetrics().horizontalAdvance(desc.text()), row.combo.x())


if __name__ == "__main__":
    unittest.main()
