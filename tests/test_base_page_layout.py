from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QSizePolicy

from ui.pages.base_page import BasePage


class BasePageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_long_subtitle_wraps_inside_narrow_viewport(self) -> None:
        subtitle = (
            "Настройка и запуск Zapret 2. В «Мои пресеты» выбирается пресет, "
            "а в «Настройка пресета» меняются профили и выбранные для них "
            "готовые стратегии."
        )
        page = BasePage("Управление Zapret 2", subtitle)
        self.addCleanup(page.deleteLater)

        page.resize(640, 360)
        page.show()
        self._app.processEvents()

        left, _top, right, _bottom = page.vBoxLayout.getContentsMargins()
        available_width = page.content.width() - left - right

        self.assertEqual(
            page.subtitle_label.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Ignored,
        )
        self.assertLessEqual(page.subtitle_label.width(), available_width)
        self.assertGreater(
            page.subtitle_label.height(),
            page.subtitle_label.fontMetrics().height(),
        )


if __name__ == "__main__":
    unittest.main()
