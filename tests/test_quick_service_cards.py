"""Быстрые карточки известных сервисов: данные, логотипы, выбор одним нажатием."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from blockcheck.quick_services import QUICK_SERVICES, companions_for, service_for_target  # noqa: E402
from blockcheck.ui.brand_icons import site_brand  # noqa: E402


class ModelTests(unittest.TestCase):
    def test_every_card_has_a_brand_logo_and_a_unique_target(self) -> None:
        for service in QUICK_SERVICES:
            self.assertIsNotNone(site_brand(service.key), service.key)
        targets = [item.target for item in QUICK_SERVICES]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(len({item.key for item in QUICK_SERVICES}), len(QUICK_SERVICES))

    def test_the_best_known_services_come_first(self) -> None:
        self.assertEqual([item.key for item in QUICK_SERVICES[:3]], ["youtube", "discord", "telegram"])

    def test_youtube_card_checks_the_page_the_previews_and_the_video_server_together(self) -> None:
        youtube = QUICK_SERVICES[0]

        self.assertEqual(youtube.target, "www.youtube.com")
        self.assertEqual(youtube.companions, (("googlevideo.com", "googlevideo.com"), ("ytimg.com", "i.ytimg.com")))
        self.assertIn("именно вашей сети", youtube.hint)

    def test_companions_follow_only_the_main_address_of_the_service(self) -> None:
        self.assertEqual(companions_for("https://www.YouTube.com/"), (("googlevideo.com", "googlevideo.com"), ("ytimg.com", "i.ytimg.com")))
        # Кто вписал один googlevideo.com или другой сайт, проверяет именно его.
        self.assertEqual(companions_for("googlevideo.com"), ())
        self.assertEqual(companions_for("discord.com"), ())

    def test_card_is_found_by_the_address_in_the_field(self) -> None:
        self.assertEqual(service_for_target("https://Discord.com/").key, "discord")
        self.assertEqual(service_for_target("www.youtube.com").key, "youtube")
        self.assertIsNone(service_for_target("example.org"))
        self.assertIsNone(service_for_target(""))


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_click_reports_the_address_and_target_marks_one_card(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        widget = QuickServiceCards()
        picked: list[str] = []
        chosen: list[str] = []
        widget.picked.connect(picked.append)
        widget.chosen.connect(chosen.append)
        discord = next(card for card in widget.cards() if card.service.key == "discord")

        discord.click()
        widget.set_target("discord.com")

        # Адрес — для поля подбора стратегии, ключ — для BlockCheck одного сервиса.
        self.assertEqual((picked, chosen), (["discord.com"], ["discord"]))
        self.assertEqual(widget.selected_key(), "discord")
        widget.set_target("example.org")
        self.assertEqual(widget.selected_key(), "")

    def test_one_card_per_service_with_logo_and_name(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        widget = QuickServiceCards()

        self.assertEqual(len(widget.cards()), len(QUICK_SERVICES))
        for card in widget.cards():
            self.assertTrue(card.logo.icon_name())
            self.assertEqual(card.name.text(), card.service.title)

    def test_card_tells_screen_readers_whether_it_is_chosen(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        widget = QuickServiceCards()
        widget.set_target("telegram.org")
        telegram = next(card for card in widget.cards() if card.service.key == "telegram")

        self.assertIn("Telegram, выбрано", telegram.accessibleName())
        self.assertIn("не выбрано", widget.cards()[0].accessibleName())


class DomainPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from unittest.mock import Mock

        from dns.ui.domain_lookup_page import DomainLookupPage

        page = DomainLookupPage(dns_feature=Mock(), embedded=True)
        self.addCleanup(page.deleteLater)
        return page

    def test_domain_check_has_no_service_cards(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        page = self._page()

        # Известные сервисы проверяет BlockCheck своими карточками; здесь — только свой домен или адрес.
        self.assertEqual(page.findChildren(QuickServiceCards), [])
        self.assertFalse(hasattr(page, "quick_cards"))


if __name__ == "__main__":
    unittest.main()
