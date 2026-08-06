from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from ui.pages.support_page import SupportPage


class SupportPageAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_support_cards_have_screen_reader_text(self) -> None:
        page = SupportPage(
            create_open_action_worker=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(page._support_group.accessibleName(), "Раздел поддержки: Forgejo Issues")
        self.assertEqual(
            page._support_group.property("screenReaderStateText"),
            "Раздел поддержки: Forgejo Issues",
        )
        self.assertEqual(page._community_group.accessibleName(), "Раздел поддержки: Каналы сообщества")
        self.assertEqual(
            page._community_group.property("screenReaderStateText"),
            "Раздел поддержки: Каналы сообщества",
        )
        self.assertEqual(page._support_card.accessibleName(), "Открыть Forgejo Issues")
        self.assertEqual(page._support_card.property("screenReaderStateText"), "Открыть Forgejo Issues")
        self.assertIn("Основной канал поддержки", page._support_card.accessibleDescription())
        self.assertEqual(page._support_card.button.accessibleName(), "Открыть Forgejo Issues")
        self.assertEqual(page._support_card.button.property("screenReaderStateText"), "Открыть Forgejo Issues")
        self.assertIn("Основной канал поддержки", page._support_card.button.accessibleDescription())
        self.assertEqual(page._tg_card.accessibleName(), "Открыть Telegram")
        self.assertEqual(page._tg_card.property("screenReaderStateText"), "Открыть Telegram")
        self.assertIn("сообществом", page._tg_card.accessibleDescription())
        self.assertEqual(page._tg_card.button.accessibleName(), "Открыть Telegram")
        self.assertEqual(page._tg_card.button.property("screenReaderStateText"), "Открыть Telegram")
        self.assertIn("сообществом", page._tg_card.button.accessibleDescription())
        self.assertEqual(page._dc_card.accessibleName(), "Открыть Discord")
        self.assertEqual(page._dc_card.property("screenReaderStateText"), "Открыть Discord")
        self.assertIn("живое общение", page._dc_card.accessibleDescription())
        self.assertEqual(page._dc_card.button.accessibleName(), "Открыть Discord")
        self.assertEqual(page._dc_card.button.property("screenReaderStateText"), "Открыть Discord")
        self.assertIn("живое общение", page._dc_card.button.accessibleDescription())

    def test_support_cards_can_be_opened_from_keyboard(self) -> None:
        requested: list[str] = []
        page = SupportPage(
            create_open_action_worker=lambda target, *_args, **_kwargs: requested.append(target),
        )

        for card, expected in (
            (page._support_card, "discussions"),
            (page._tg_card, "telegram"),
            (page._dc_card, "discord"),
        ):
            with self.subTest(expected=expected):
                card.clicked.disconnect()
                card.clicked.connect(lambda _checked=False, value=expected: requested.append(value))
                self.assertEqual(card.focusPolicy(), Qt.FocusPolicy.StrongFocus)
                card.keyPressEvent(
                    QKeyEvent(
                        QEvent.Type.KeyPress,
                        Qt.Key.Key_Return,
                        Qt.KeyboardModifier.NoModifier,
                    )
                )
                self.assertEqual(requested[-1], expected)

    def test_language_refresh_keeps_screen_reader_text(self) -> None:
        page = SupportPage(
            create_open_action_worker=lambda *_args, **_kwargs: None,
        )

        page.set_ui_language("ru")

        self.assertEqual(page._support_group.accessibleName(), "Раздел поддержки: Forgejo Issues")
        self.assertEqual(
            page._support_group.property("screenReaderStateText"),
            "Раздел поддержки: Forgejo Issues",
        )
        self.assertEqual(page._community_group.accessibleName(), "Раздел поддержки: Каналы сообщества")
        self.assertEqual(
            page._community_group.property("screenReaderStateText"),
            "Раздел поддержки: Каналы сообщества",
        )
        self.assertEqual(page._support_card.accessibleName(), "Открыть Forgejo Issues")
        self.assertEqual(page._support_card.property("screenReaderStateText"), "Открыть Forgejo Issues")
        self.assertIn("Основной канал поддержки", page._support_card.accessibleDescription())
        self.assertEqual(page._support_card.button.accessibleName(), "Открыть Forgejo Issues")
        self.assertEqual(page._support_card.button.property("screenReaderStateText"), "Открыть Forgejo Issues")
        self.assertIn("Основной канал поддержки", page._support_card.button.accessibleDescription())
        self.assertEqual(page._tg_card.accessibleName(), "Открыть Telegram")
        self.assertEqual(page._tg_card.property("screenReaderStateText"), "Открыть Telegram")
        self.assertIn("сообществом", page._tg_card.accessibleDescription())
        self.assertEqual(page._tg_card.button.accessibleName(), "Открыть Telegram")
        self.assertEqual(page._tg_card.button.property("screenReaderStateText"), "Открыть Telegram")
        self.assertIn("сообществом", page._tg_card.button.accessibleDescription())
        self.assertEqual(page._dc_card.accessibleName(), "Открыть Discord")
        self.assertEqual(page._dc_card.property("screenReaderStateText"), "Открыть Discord")
        self.assertIn("живое общение", page._dc_card.accessibleDescription())
        self.assertEqual(page._dc_card.button.accessibleName(), "Открыть Discord")
        self.assertEqual(page._dc_card.button.property("screenReaderStateText"), "Открыть Discord")
        self.assertIn("живое общение", page._dc_card.button.accessibleDescription())


if __name__ == "__main__":
    unittest.main()
