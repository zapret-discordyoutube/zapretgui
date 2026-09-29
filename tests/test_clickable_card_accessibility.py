from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication


class ClickableCardAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _press_key(self, widget, key: Qt.Key) -> None:
        event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        widget.keyPressEvent(event)

    def test_control_summary_clickable_item_works_from_keyboard(self) -> None:
        from presets.ui.control.top_summary_widget import ControlTopSummaryItem

        item = ControlTopSummaryItem(icon_name="fa5s.folder-open", clickable=True)
        clicks: list[bool] = []
        item.clicked.connect(lambda: clicks.append(True))
        item.set_texts(caption="Текущий preset", value="Default", details="")

        self.assertEqual(item.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        self.assertEqual(item.accessibleName(), "Текущий preset: Default")
        self.assertEqual(item.property("screenReaderStateText"), "Текущий preset: Default")
        self.assertIn("Enter или Пробел", item.accessibleDescription())
        self.assertIn("открыть связанный раздел", item.accessibleDescription())

        self._press_key(item, Qt.Key.Key_Return)

        self.assertEqual(clicks, [True])


if __name__ == "__main__":
    unittest.main()
