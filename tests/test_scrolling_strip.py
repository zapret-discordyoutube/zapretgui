"""ScrollingStrip: вкладки остаются в одну строку и прокручиваются вбок, а не режутся молча."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


def _tabs(count: int = 8, width: int = 100) -> tuple[QWidget, list[QPushButton]]:
    content = QWidget()
    layout = QHBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    buttons = []
    for index in range(count):
        button = QPushButton(f"tab {index}", content)
        button.setFixedSize(width, 30)
        layout.addWidget(button)
        buttons.append(button)
    return content, buttons


class ScrollingStripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _strip(self, viewport_width: int):
        from ui.widgets.scrolling_strip import ScrollingStrip

        content, buttons = _tabs()
        strip = ScrollingStrip(content)
        strip.resize(viewport_width, 30)
        strip.show()
        self._app.processEvents()
        return strip, content, buttons

    def test_wants_the_full_width_of_the_tabs_but_accepts_less(self) -> None:
        strip, content, _ = self._strip(900)
        self.assertEqual(strip.sizeHint().width(), 800)
        self.assertLess(strip.minimumSizeHint().width(), 200)

    def test_fits_without_scrolling_when_there_is_room(self) -> None:
        strip, content, _ = self._strip(900)
        self.assertEqual(strip.horizontalScrollBar().maximum(), 0)
        self.assertEqual(content.width(), 800)

    def test_narrow_strip_keeps_every_tab_reachable_by_scrolling(self) -> None:
        strip, content, _ = self._strip(300)
        self.assertEqual(content.width(), 800)  # вкладки не сжимаются и не накладываются
        self.assertEqual(strip.horizontalScrollBar().maximum(), 500)

    def test_selected_tab_is_scrolled_into_view(self) -> None:
        strip, _, buttons = self._strip(300)
        self.assertEqual(strip.horizontalScrollBar().value(), 0)
        strip.ensure_visible(buttons[7])
        self._app.processEvents()
        self.assertGreater(strip.horizontalScrollBar().value(), 0)
        visible = strip.viewport().rect()
        left = buttons[7].mapTo(strip.viewport(), buttons[7].rect().topLeft()).x()
        self.assertTrue(visible.left() <= left and left + 100 <= visible.right() + 1)

    def test_sync_follows_a_changed_set_of_tabs(self) -> None:
        strip, content, buttons = self._strip(900)
        for button in buttons[4:]:
            button.hide()
        strip.sync()
        self.assertEqual(content.width(), 400)


if __name__ == "__main__":
    unittest.main()
