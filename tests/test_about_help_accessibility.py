from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, HyperlinkCard, PrimaryPushSettingCard, PushSettingCard, SettingCardGroup

from ui.pages.about_page_help_build import build_about_page_help_content
from ui.theme import get_theme_tokens


class AboutHelpAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _build(self, opened: list[str]):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        layout = QVBoxLayout(parent)
        return build_about_page_help_content(
            layout,
            tr_fn=lambda _key, default: default,
            tokens=get_theme_tokens(),
            content_parent=parent,
            make_section_label=lambda text: QWidget(),
            push_setting_card_cls=PushSettingCard,
            primary_push_setting_card_cls=PrimaryPushSettingCard,
            setting_card_group_cls=SettingCardGroup,
            on_open_link=opened.append,
        )

    def test_links_are_grouped_learn_ask_follow(self) -> None:
        widgets = self._build([])

        self.assertEqual(list(widgets.groups), ["learn", "ask", "news"])
        for key, title in (("learn", "Научиться"), ("ask", "Спросить"), ("news", "Следить за новостями")):
            with self.subTest(group=key):
                self.assertEqual(widgets.groups[key].accessibleName(), f"Раздел справки: {title}")
                self.assertEqual(widgets.groups[key].property("screenReaderStateText"), f"Раздел справки: {title}")

        self.assertEqual(
            list(widgets.cards),
            [
                "forum_for_beginners", "youtube_course", "android_guide",
                "chats_folder", "support_telegram", "support_discord", "support_discussions",
                "links_channel", "telegram_news", "mastodon", "bastyon", "source_code",
            ],
        )
        # Главные «всё в одном месте» ссылки выделены акцентом и стоят первыми в своих группах.
        self.assertIsInstance(widgets.cards["chats_folder"], PrimaryPushSettingCard)
        self.assertIsInstance(widgets.cards["links_channel"], PrimaryPushSettingCard)
        self.assertNotIsInstance(widgets.cards["telegram_news"], PrimaryPushSettingCard)

    def test_help_cards_have_screen_reader_text(self) -> None:
        widgets = self._build([])

        expected = {
            "forum_for_beginners": ("Открыть вики-сайт", "Документация и инструкции"),
            "youtube_course": ("Открыть видеокурс на YouTube", "Все видео курса"),
            "android_guide": ("Открыть инструкцию для Android", "Открыть инструкцию на сайте"),
            "chats_folder": ("Открыть папку со всеми чатами в Telegram", "одной папкой"),
            "support_telegram": ("Открыть Telegram-чат", "Быстрые вопросы"),
            "support_discord": ("Открыть Discord", "Обсуждение и живое общение"),
            "support_discussions": ("Открыть Forgejo Issues", "Forgejo Issues"),
            "links_channel": ("Открыть канал со всеми ссылками", "в одном месте"),
            "telegram_news": ("Открыть Telegram канал", "Новости и обновления"),
            "mastodon": ("Открыть Mastodon профиль", "Новости в Fediverse"),
            "bastyon": ("Открыть Bastyon профиль", "Новости в Bastyon"),
            "source_code": ("Открыть исходный код в Forgejo", "Репозиторий программы"),
        }
        for action, (name, description) in expected.items():
            card = widgets.cards[action]
            with self.subTest(action=action):
                self.assertEqual(card.accessibleName(), name)
                self.assertEqual(card.property("screenReaderStateText"), name)
                self.assertIn(description, card.accessibleDescription())
                self.assertEqual(card.button.accessibleName(), name)
                self.assertEqual(card.button.text(), "Открыть")

    def test_every_card_opens_its_own_action(self) -> None:
        opened: list[str] = []
        widgets = self._build(opened)

        for card in widgets.cards.values():
            card.button.click()

        self.assertEqual(opened, list(widgets.cards))

    def test_every_help_action_has_an_opener_and_hubs_point_to_telegram(self) -> None:
        from app.page_names import PageName
        from ui.page_deps.system import build_about_page_kwargs

        class _Feature:
            def __init__(self) -> None:
                self.actions: dict[str, object] = {}

            def create_external_action_worker(self, request_id, *, action_name, action_fn, parent=None):
                self.actions[action_name] = action_fn
                return None

        feature = _Feature()
        kwargs = build_about_page_kwargs(
            page_name=PageName.ABOUT,
            external_actions_feature=feature,
            show_page=lambda *_args, **_kwargs: None,
            ui_state_store=None,
        )
        widgets = self._build([])
        for index, action in enumerate(widgets.cards):
            kwargs["create_open_action_worker"](index, action_name=action)
        self.assertEqual(set(feature.actions), set(widgets.cards))

        with patch("config.telegram_links.open_telegram_link") as open_link:
            feature.actions["chats_folder"]()
            feature.actions["links_channel"]()
        self.assertEqual(open_link.call_args_list[0].kwargs.get("slug"), "xjPs164MI7AxZWE6")
        self.assertEqual(open_link.call_args_list[1].args[0], "runetvpnyoutubediscord")

    def test_hyperlink_cards_can_be_opened_from_keyboard(self) -> None:
        from ui.pages.about_page_help_accessibility import set_help_card_accessibility

        card = HyperlinkCard(
            "https://example.com/help",
            "Открыть",
            FluentIcon.LINK,
            "Тестовая ссылка",
            "Описание ссылки",
        )
        self.addCleanup(card.deleteLater)
        set_help_card_accessibility(
            card,
            action_name="Открыть тестовую ссылку",
            description="Открывает тестовую ссылку.",
        )
        opened: list[bool] = []
        card.linkButton.clicked.connect(lambda: opened.append(True))

        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        with patch("PyQt6.QtGui.QDesktopServices.openUrl", return_value=True):
            card.keyPressEvent(event)

        self.assertTrue(event.isAccepted())
        self.assertEqual(opened, [True])


if __name__ == "__main__":
    unittest.main()
