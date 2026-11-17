"""Описание страницы не раздувается, когда в странице мало блоков, а окно высокое."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui.pages.base_page import BasePage


class BasePageSubtitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _page(self, width: int, height: int) -> BasePage:
        page = BasePage("Заголовок", "Какие сайты открываются, почему не открываются остальные и что с этим делать")
        self.addCleanup(page.deleteLater)
        page.resize(width, height)
        page.show()
        self._app.processEvents()
        self._app.processEvents()
        return page

    def test_subtitle_keeps_its_text_height_on_a_tall_window(self) -> None:
        # Лишнее место страницы раздавалось описанию (BlockCheck, 2026-11-17: пустота в 350 px).
        page = self._page(1300, 900)
        label = page.subtitle_label

        self.assertEqual(label.height(), label.heightForWidth(label.width()))
        self.assertLess(label.height(), 30)

    def test_subtitle_still_wraps_on_a_narrow_window(self) -> None:
        page = self._page(300, 900)
        label = page.subtitle_label

        self.assertGreater(label.height(), 40)
        self.assertEqual(label.height(), label.heightForWidth(label.width()))


if __name__ == "__main__":
    unittest.main()
