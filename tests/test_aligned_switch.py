from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.widgets.aligned_switch import SWITCH_OFF_TEXT, SWITCH_ON_TEXT, AlignedSwitchButton


class AlignedSwitchButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _switch(self) -> AlignedSwitchButton:
        switch = AlignedSwitchButton()
        self.addCleanup(switch.deleteLater)
        switch.show()
        QApplication.processEvents()
        return switch

    def test_uses_project_texts(self) -> None:
        switch = self._switch()

        self.assertEqual(switch.onText, SWITCH_ON_TEXT)
        self.assertEqual(switch.offText, SWITCH_OFF_TEXT)
        self.assertEqual(switch.label.text(), SWITCH_OFF_TEXT)

    def test_indicator_is_not_a_separate_tab_stop(self) -> None:
        from PyQt6.QtCore import Qt

        switch = self._switch()

        self.assertEqual(switch.indicator.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_width_does_not_change_between_on_and_off(self) -> None:
        switch = self._switch()
        off_width = switch.sizeHint().width()
        off_label_width = switch.label.width()

        switch.setChecked(True)
        QApplication.processEvents()

        self.assertEqual(switch.label.text(), SWITCH_ON_TEXT)
        self.assertEqual(switch.sizeHint().width(), off_width)
        self.assertEqual(switch.label.width(), off_label_width)

    def test_label_fits_longest_text_after_font_change(self) -> None:
        switch = self._switch()
        font = switch.label.font()
        font.setPointSize(font.pointSize() + 6)
        switch.label.setFont(font)
        QApplication.processEvents()

        metrics = switch.label.fontMetrics()
        longest = max(metrics.horizontalAdvance(SWITCH_ON_TEXT), metrics.horizontalAdvance(SWITCH_OFF_TEXT))
        self.assertEqual(switch.label.width(), longest)

    def test_settings_rows_keep_switch_in_same_place_for_on_and_off(self) -> None:
        from ui.widgets.win11_controls import Win11ToggleRow

        host = QWidget()
        self.addCleanup(host.deleteLater)
        layout = QVBoxLayout(host)
        on_row = Win11ToggleRow("fa5s.bolt", "Включено")
        off_row = Win11ToggleRow("fa5s.bolt", "Выключено")
        on_row.setChecked(True)
        layout.addWidget(on_row)
        layout.addWidget(off_row)
        host.resize(600, 200)
        host.show()
        QApplication.processEvents()

        self.assertIsInstance(on_row._switch_button, AlignedSwitchButton)
        on_x = on_row._switch_button.indicator.mapTo(host, on_row._switch_button.indicator.rect().topLeft()).x()
        off_x = off_row._switch_button.indicator.mapTo(host, off_row._switch_button.indicator.rect().topLeft()).x()
        self.assertEqual(on_x, off_x)


if __name__ == "__main__":
    unittest.main()
