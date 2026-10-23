from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from ui.pages.base_page import BasePage


class BasePageAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_page_scrollbar_arrow_buttons_do_not_take_tab_focus(self) -> None:
        page = BasePage("Тестовая страница")
        self.addCleanup(page.deleteLater)

        buttons = [
            child
            for child in page.findChildren(object)
            if type(child).__name__ == "ArrowButton"
            and hasattr(child, "setFocusPolicy")
        ]

        self.assertTrue(buttons)
        self.assertTrue(all(button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in buttons))

    def test_page_titles_expose_screen_reader_state_text(self) -> None:
        page = BasePage("Тестовая страница", "Описание тестовой страницы")
        self.addCleanup(page.deleteLater)

        section = page.add_section_title("Основные настройки", return_widget=True)

        self.assertEqual(
            page.title_label.property("screenReaderStateText"),
            "Заголовок страницы: Тестовая страница",
        )
        self.assertEqual(
            page.subtitle_label.property("screenReaderStateText"),
            "Описание страницы: Описание тестовой страницы",
        )
        self.assertEqual(
            section.property("screenReaderStateText"),
            "Раздел страницы: Основные настройки",
        )

    def test_page_finds_first_keyboard_control_for_tab_navigation(self) -> None:
        page = BasePage("Тестовая страница")
        self.addCleanup(page.deleteLater)

        label = QLabel("Только текст", page.content)
        page.layout.addWidget(label)
        first_button = QPushButton("Первая кнопка", page.content)
        second_button = QPushButton("Вторая кнопка", page.content)
        page.layout.addWidget(first_button)
        page.layout.addWidget(second_button)
        page.show()
        self._app.processEvents()

        self.assertIs(page._first_keyboard_focus_control(), first_button)

    def test_focus_candidates_come_lazily_in_find_children_order(self) -> None:
        # Рядом с занятым потоком каждый обёрнутый в Python виджет отдаёт замок
        # интерпретатора, поэтому поиск не должен оборачивать всю страницу
        # (findChildren), а обязан остановиться на первом подходящем виджете.
        page = BasePage("Тестовая страница")
        self.addCleanup(page.deleteLater)
        group = QWidget(page.content)
        page.layout.addWidget(group)
        QLabel("Текст", group)
        nested_button = QPushButton("Вложенная", group)
        for index in range(30):
            QPushButton(f"Кнопка {index}", page.content)

        page.show()
        self._app.processEvents()
        candidates = page._iter_keyboard_focus_candidates()

        self.assertTrue(inspect.isgenerator(candidates))
        self.assertEqual(list(candidates), page.content.findChildren(QWidget))
        self.assertIs(page._first_keyboard_focus_control(), nested_button)

    def test_page_focuses_first_keyboard_control_when_focus_is_outside(self) -> None:
        outside_button = QPushButton("Снаружи")
        self.addCleanup(outside_button.deleteLater)
        outside_button.show()

        page = BasePage("Тестовая страница")
        self.addCleanup(page.deleteLater)
        first_button = QPushButton("Первая кнопка", page.content)
        page.layout.addWidget(first_button)
        page.show()
        self._app.processEvents()

        outside_button.setFocus()
        self._app.processEvents()
        page._focus_first_keyboard_control_if_needed()

        self.assertIs(self._app.focusWidget(), first_button)

    def test_page_keeps_existing_focus_inside_page(self) -> None:
        page = BasePage("Тестовая страница")
        self.addCleanup(page.deleteLater)
        first_button = QPushButton("Первая кнопка", page.content)
        second_button = QPushButton("Вторая кнопка", page.content)
        page.layout.addWidget(first_button)
        page.layout.addWidget(second_button)
        page.show()
        self._app.processEvents()

        second_button.setFocus()
        self._app.processEvents()
        page._focus_first_keyboard_control_if_needed()

        self.assertIs(self._app.focusWidget(), second_button)

    def test_page_title_screen_reader_text_updates_with_language(self) -> None:
        def _tr(key: str, *, language: str, default: str) -> str:
            texts = {
                ("test.title", "ru"): "Русский заголовок",
                ("test.title", "en"): "English title",
                ("test.subtitle", "ru"): "Русское описание",
                ("test.subtitle", "en"): "English description",
                ("test.section", "ru"): "Русский раздел",
                ("test.section", "en"): "English section",
            }
            return texts.get((key, language), default)

        with patch("ui.pages.base_page.tr_catalog", side_effect=_tr):
            page = BasePage(
                "Запасной заголовок",
                "Запасное описание",
                title_key="test.title",
                subtitle_key="test.subtitle",
            )
            self.addCleanup(page.deleteLater)
            section = page.add_section_title(
                "Запасной раздел",
                return_widget=True,
                text_key="test.section",
            )

            page.set_ui_language("en")

        self.assertEqual(
            page.title_label.property("screenReaderStateText"),
            "Заголовок страницы: English title",
        )
        self.assertEqual(
            page.subtitle_label.property("screenReaderStateText"),
            "Описание страницы: English description",
        )
        self.assertEqual(
            section.property("screenReaderStateText"),
            "Раздел страницы: English section",
        )


if __name__ == "__main__":
    unittest.main()
