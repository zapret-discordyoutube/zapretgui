from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.pages.about_page_support_build import build_about_page_support_content
from ui.theme import get_theme_tokens


class AboutSupportAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_support_cards_have_screen_reader_text(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)

        widgets = build_about_page_support_content(
            layout,
            tr_fn=lambda _key, default: default,
            content_parent=parent,
            tokens=get_theme_tokens(),
            on_open_discussions=lambda: None,
            on_open_telegram=lambda: None,
            on_open_discord=lambda: None,
        )

        self.assertEqual(widgets.discussions_group.accessibleName(), "Раздел поддержки: Forgejo Issues")
        self.assertEqual(
            widgets.discussions_group.property("screenReaderStateText"),
            "Раздел поддержки: Forgejo Issues",
        )
        self.assertEqual(widgets.community_group.accessibleName(), "Раздел поддержки: Каналы сообщества")
        self.assertEqual(
            widgets.community_group.property("screenReaderStateText"),
            "Раздел поддержки: Каналы сообщества",
        )
        self.assertEqual(widgets.discussions_card.accessibleName(), "Открыть Forgejo Issues")
        self.assertEqual(widgets.discussions_card.property("screenReaderStateText"), "Открыть Forgejo Issues")
        self.assertIn("Основной канал поддержки", widgets.discussions_card.accessibleDescription())
        self.assertEqual(widgets.discussions_card.button.accessibleName(), "Открыть Forgejo Issues")
        self.assertEqual(
            widgets.discussions_card.button.property("screenReaderStateText"),
            "Открыть Forgejo Issues",
        )
        self.assertIn("Основной канал поддержки", widgets.discussions_card.button.accessibleDescription())
        self.assertEqual(widgets.telegram_card.accessibleName(), "Открыть Telegram")
        self.assertEqual(widgets.telegram_card.property("screenReaderStateText"), "Открыть Telegram")
        self.assertIn("сообществом", widgets.telegram_card.accessibleDescription())
        self.assertEqual(widgets.telegram_card.button.accessibleName(), "Открыть Telegram")
        self.assertEqual(widgets.telegram_card.button.property("screenReaderStateText"), "Открыть Telegram")
        self.assertIn("сообществом", widgets.telegram_card.button.accessibleDescription())
        self.assertEqual(widgets.discord_card.accessibleName(), "Открыть Discord")
        self.assertEqual(widgets.discord_card.property("screenReaderStateText"), "Открыть Discord")
        self.assertIn("живое общение", widgets.discord_card.accessibleDescription())
        self.assertEqual(widgets.discord_card.button.accessibleName(), "Открыть Discord")
        self.assertEqual(widgets.discord_card.button.property("screenReaderStateText"), "Открыть Discord")
        self.assertIn("живое общение", widgets.discord_card.button.accessibleDescription())

    def test_support_cards_can_be_opened_from_keyboard(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)
        opened: list[str] = []

        widgets = build_about_page_support_content(
            layout,
            tr_fn=lambda _key, default: default,
            content_parent=parent,
            tokens=get_theme_tokens(),
            on_open_discussions=lambda: opened.append("discussions"),
            on_open_telegram=lambda: opened.append("telegram"),
            on_open_discord=lambda: opened.append("discord"),
        )

        for card, expected in (
            (widgets.discussions_card, "discussions"),
            (widgets.telegram_card, "telegram"),
            (widgets.discord_card, "discord"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(card.focusPolicy(), Qt.FocusPolicy.StrongFocus)
                card.keyPressEvent(
                    QKeyEvent(
                        QEvent.Type.KeyPress,
                        Qt.Key.Key_Return,
                        Qt.KeyboardModifier.NoModifier,
                    )
                )
                self.assertEqual(opened[-1], expected)


if __name__ == "__main__":
    unittest.main()
