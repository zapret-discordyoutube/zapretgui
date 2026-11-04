"""Telegram закрыт по IP-адресам: подсказка «стратегия не поможет» ведёт к прокси и ZaStoGram, а не к hosts и DNS."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from hosts.geo_sites import GeoSites, build_geo_sites, geo_services_for_site_list, only_proxy_helps, only_vpn_helps


EMPTY_CATALOG = {"services": (), "has_proxy_by_service": {}, "domain_names_by_service": {}}


class TelegramGeoSitesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geo = build_geo_sites(EMPTY_CATALOG)

    def test_telegram_sites_and_subdomains_are_found(self) -> None:
        for host in ("telegram.org", "web.telegram.org", "t.me", "https-less.t.me", "www.telegram.org"):
            self.assertEqual(self.geo.service_for(host), "Telegram", host)

    def test_profile_name_in_any_spelling_is_telegram(self) -> None:
        for name in ("Telegram", "telegram", "Телеграм", "телеграмм", "ТГ", "tg"):
            self.assertEqual(self.geo.services_for_name(name), ("Telegram",), name)
        self.assertEqual(self.geo.services_for_name("Мой профиль"), ())

    def test_telegram_site_list_is_a_telegram_list(self) -> None:
        text = "telegram.org\nt.me\ncdn.telegram.org\ntelesco.pe\n"
        self.assertEqual(geo_services_for_site_list(self.geo, text), ("Telegram",))

    def test_telegram_needs_a_proxy_not_a_vpn_and_not_hosts(self) -> None:
        self.assertTrue(only_proxy_helps(("Telegram",)))
        self.assertFalse(only_vpn_helps(("Telegram",)))
        self.assertFalse(only_proxy_helps(("Telegram", "Gemini AI")))
        self.assertFalse(only_proxy_helps(()))

    def test_site_check_does_not_treat_telegram_as_a_geo_site(self) -> None:
        # Проверка сайтов решает по виду блокировки: у блокировки по имени Telegram стратегия помогает.
        geo = GeoSites({"telegram.org": "Telegram", "claude.ai": "Claude"})
        self.assertEqual(geo.service_for_diagnostics("telegram.org"), "")
        self.assertEqual(geo.service_for_diagnostics("claude.ai"), "Claude")
        self.assertEqual(self.geo.service_for_diagnostics("web.telegram.org"), "")
        self.assertEqual(self.geo.service_for("web.telegram.org"), "Telegram")


class TelegramNoticeTextTests(unittest.TestCase):
    def test_text_names_proxy_and_zastogram_and_not_hosts(self) -> None:
        from profile.ui.profile_geo_notice import geo_notice_text, geo_notice_title

        self.assertEqual(geo_notice_title(("Telegram",)), "Telegram: стратегия не поможет")
        text = geo_notice_text(("Telegram",))
        self.assertIn("IP-адресам", text)
        self.assertIn("«Telegram Proxy»", text)
        self.assertIn("@zastogram", text)
        self.assertNotIn("Редакторе hosts", text)

    def test_other_services_keep_their_text(self) -> None:
        from profile.ui.profile_geo_notice import geo_notice_text

        self.assertIn("«Редакторе hosts»", geo_notice_text(("Gemini AI",)))
        self.assertNotIn("zastogram", geo_notice_text(("Gemini AI",)))

    def test_scan_question_for_telegram_offers_the_proxy(self) -> None:
        from blockcheck.strategy_scan_page_plans import geo_site_question_text

        text = geo_site_question_text("Telegram", "telegram.org", language="ru")
        self.assertIn("«Telegram Proxy»", text)
        self.assertIn("@zastogram", text)
        self.assertIn("telegram.org", text)
        self.assertNotIn("«Редакторе hosts»", text)
        self.assertIn("«Telegram Proxy»", geo_site_question_text("Telegram", "t.me", language="ru"))
        self.assertIn("Telegram Proxy section", geo_site_question_text("Telegram", "t.me", language="en"))


class TelegramNoticeCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from profile.ui.profile_geo_notice import ProfileGeoNotice

        self.host = QWidget()
        self.host.resize(900, 600)
        self.open_hosts = Mock()
        self.open_proxy = Mock()
        self.open_zastogram = Mock()
        self.notice = ProfileGeoNotice(
            self.host,
            open_hosts_editor=self.open_hosts,
            open_dns_settings=Mock(),
            open_telegram_proxy=self.open_proxy,
            open_zastogram=self.open_zastogram,
        )
        self.notice._runtime.is_current = lambda *_args, **_kwargs: True
        self.host.show()
        self.addCleanup(self.host.deleteLater)

    def _answer(self, services: tuple[str, ...]) -> None:
        self.notice.request("profile:x")
        self.notice._on_loaded(1, "profile:x", services)
        self.notice.card.finish_appearing()

    def test_telegram_card_has_proxy_buttons_instead_of_hosts_and_dns(self) -> None:
        card = self.notice.card
        self._answer(("Telegram",))

        self.assertTrue(card.isVisible())
        self.assertEqual(card.title_label.text(), "Telegram: стратегия не поможет")
        self.assertFalse(card.hosts_button.isVisibleTo(card))
        self.assertFalse(card.dns_button.isVisibleTo(card))
        self.assertTrue(card.proxy_button.isVisibleTo(card))
        self.assertTrue(card.zastogram_button.isVisibleTo(card))

    def test_other_service_keeps_hosts_and_dns_buttons(self) -> None:
        card = self.notice.card
        self._answer(("Gemini AI",))

        self.assertTrue(card.hosts_button.isVisibleTo(card))
        self.assertTrue(card.dns_button.isVisibleTo(card))
        self.assertFalse(card.proxy_button.isVisibleTo(card))
        self.assertFalse(card.zastogram_button.isVisibleTo(card))

    def test_buttons_open_the_proxy_page_and_zastogram(self) -> None:
        card = self.notice.card
        self._answer(("Telegram",))

        card.proxy_button.click()
        card.zastogram_button.click()

        self.open_proxy.assert_called_once_with()
        self.open_zastogram.assert_called_once_with()
        self.open_hosts.assert_not_called()

    def test_without_own_handlers_the_card_opens_the_page_and_the_link_itself(self) -> None:
        from profile.ui.profile_geo_notice import ProfileGeoNotice

        notice = ProfileGeoNotice(self.host)
        self.addCleanup(notice.cleanup)
        with patch("profile.ui.profile_geo_notice.show_telegram_proxy_page") as show_page:
            notice._show_telegram_proxy_page()
        show_page.assert_called_once_with(self.host.window())

        from config.urls import ZASTOGRAM_URL

        with patch("updater.ui.update_dialog.open_url_in_background") as open_link:
            notice._open_zastogram()
        open_link.assert_called_once_with(ZASTOGRAM_URL)

    def test_one_url_for_zastogram_everywhere(self) -> None:
        from config.urls import ZASTOGRAM_URL
        from telegram_proxy.ui import page as proxy_page

        self.assertEqual(proxy_page._ZASTOGRAM_URL, ZASTOGRAM_URL)
        self.assertTrue(ZASTOGRAM_URL.startswith("https://git.zapret.moe/zastogram/"))


if __name__ == "__main__":
    unittest.main()
