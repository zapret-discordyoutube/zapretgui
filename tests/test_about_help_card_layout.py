"""Карточка «Нужна помощь?» на странице «О программе» не обрезает описание.

Живой случай (Dev 21.1.7.268/269 на Windows 10, 1280x800, масштаб 100 %): после того как
описанию включили перенос строк, подпись осталась шириной ~190 px и одной строкой по
высоте, а вся свободная ширина карточки пустовала: текст налезал на заголовок и
обрезался. Снимок экрана это показал, тесты на перенос слов — нет.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.pages.about_page_about_build import build_about_page_about_content
from ui.theme import get_theme_tokens


class HelpCardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _page(self, width: int):
        parent = QWidget()
        layout = QVBoxLayout(parent)
        widgets = build_about_page_about_content(
            layout,
            tr_fn=lambda _key, default: default,
            tokens=get_theme_tokens(),
            content_parent=parent,
            app_version="9.9.9",
            make_section_label=lambda text: QWidget(),
            on_open_updates=lambda: None,
            on_open_whats_new=lambda: None,
            on_open_premium=lambda: None,
            on_open_kvn_tab=lambda: None,
            on_open_help_tab=lambda: None,
        )
        parent.resize(width, 700)
        parent.show()
        self._app.processEvents()
        self._app.processEvents()
        return parent, widgets.help_card

    def assert_text_fits(self, card, width: int) -> None:
        label = card.contentLabel
        need = label.heightForWidth(label.width())
        self.assertGreaterEqual(label.height(), need, f"на странице {width} px описание обрезано по высоте")
        top = label.mapTo(card, label.rect().topLeft()).y()
        title_bottom = card.titleLabel.mapTo(card, card.titleLabel.rect().bottomLeft()).y()
        self.assertGreaterEqual(top, title_bottom, f"на странице {width} px описание налезает на заголовок")
        self.assertLessEqual(top + label.height(), card.height(), f"на странице {width} px описание выходит за карточку")

    def test_description_is_not_clipped_at_any_page_width(self) -> None:
        for width in (1000, 700, 520, 400):
            with self.subTest(width=width):
                parent, card = self._page(width)
                try:
                    self.assert_text_fits(card, width)
                finally:
                    parent.close()

    def test_description_uses_the_free_width_on_a_wide_page(self) -> None:
        parent, card = self._page(1000)
        try:
            label = card.contentLabel
            # Одна строка: ширина подписи не меньше ширины всего текста.
            self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        finally:
            parent.close()

    def test_card_grows_when_the_text_needs_more_lines(self) -> None:
        wide_parent, wide = self._page(1000)
        narrow_parent, narrow = self._page(400)
        try:
            self.assertEqual(wide.height(), 70)
            self.assertGreater(narrow.height(), wide.height())
        finally:
            wide_parent.close()
            narrow_parent.close()


if __name__ == "__main__":
    unittest.main()
