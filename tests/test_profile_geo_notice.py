"""Подсказка на странице профиля: гео-сервису нужен hosts или DNS, а не стратегия."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from hosts.geo_sites import GeoSites, geo_services_for_site_list


GEO = GeoSites({"gemini.google.com": "Gemini AI", "chatgpt.com": "ChatGPT", "claude.ai": "Claude"})


class GeoServicesForSiteListTests(unittest.TestCase):
    def test_list_of_one_geo_service_names_it(self) -> None:
        self.assertEqual(geo_services_for_site_list(GEO, "# Gemini\n\ngemini.google.com\n"), ("Gemini AI",))

    def test_subdomains_and_extra_addresses_still_count(self) -> None:
        text = "chatgpt.com\ncdn.chatgpt.com\noaistatic.com\n"
        self.assertEqual(geo_services_for_site_list(GEO, text), ("ChatGPT",))

    def test_services_go_by_number_of_addresses(self) -> None:
        text = "claude.ai\nchatgpt.com\napi.chatgpt.com\n"
        self.assertEqual(geo_services_for_site_list(GEO, text), ("ChatGPT", "Claude"))

    def test_big_general_list_with_a_stray_geo_address_is_not_geo(self) -> None:
        text = "chatgpt.com\n" + "\n".join(f"site{number}.example" for number in range(40))
        self.assertEqual(geo_services_for_site_list(GEO, text), ())

    def test_ordinary_and_empty_lists_are_not_geo(self) -> None:
        self.assertEqual(geo_services_for_site_list(GEO, "youtube.com\ndiscord.com\n"), ())
        self.assertEqual(geo_services_for_site_list(GEO, ""), ())


class GeoNoticeTextTests(unittest.TestCase):
    def test_title_names_service_and_counts_the_rest(self) -> None:
        from profile.ui.profile_geo_notice import geo_notice_text, geo_notice_title

        self.assertEqual(geo_notice_title(("Gemini AI",)), "Gemini AI: стратегия не поможет")
        self.assertEqual(geo_notice_title(("A", "B", "C", "D")), "A, B и ещё 2: стратегия не поможет")
        self.assertIn("Этот сервис сам", geo_notice_text(("Gemini AI",)))
        self.assertIn("Эти сервисы сами", geo_notice_text(("A", "B")))
        self.assertIn("«Редакторе hosts»", geo_notice_text(("A",)))


class ProfileGeoNoticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from profile.ui.profile_geo_notice import ProfileGeoNotice

        self.host = QWidget()
        self.host.resize(900, 600)
        self.open_hosts = Mock()
        self.open_dns = Mock()
        self.notice = ProfileGeoNotice(
            self.host,
            open_hosts_editor=self.open_hosts,
            open_dns_settings=self.open_dns,
        )
        self.notice._runtime.is_current = lambda *_args, **_kwargs: True
        self.host.show()
        self.addCleanup(self.host.deleteLater)

    def _answer(self, profile_key: str, services: tuple[str, ...]) -> None:
        self.notice.request(profile_key)
        self.notice._on_loaded(1, profile_key, services)

    def test_card_is_hidden_until_geo_service_is_found(self) -> None:
        self.assertFalse(self.notice.card.isVisible())
        self._answer("profile:youtube", ())
        self.assertFalse(self.notice.card.isVisible())

    def test_geo_profile_shows_card_in_the_bottom_right_corner(self) -> None:
        from profile.ui.profile_geo_notice import CARD_MARGIN

        self._answer("profile:gemini", ("Gemini AI",))
        card = self.notice.card
        card._slide.stop()
        card.place(animated=False)

        self.assertTrue(card.isVisible())
        self.assertEqual(card.title_label.text(), "Gemini AI: стратегия не поможет")
        self.assertEqual(card.geometry().right() + 1, self.host.width() - CARD_MARGIN)
        self.assertEqual(card.geometry().bottom() + 1, self.host.height() - CARD_MARGIN)

        self.host.resize(1200, 700)
        self._app.processEvents()
        self.assertEqual(card.geometry().right() + 1, 1200 - CARD_MARGIN)
        self.assertEqual(card.geometry().bottom() + 1, 700 - CARD_MARGIN)

    def test_card_is_a_child_of_the_page_not_a_separate_window(self) -> None:
        self._answer("profile:gemini", ("Gemini AI",))
        self.assertIs(self.notice.card.parentWidget(), self.host)
        self.assertFalse(self.notice.card.isWindow())
        self.assertFalse(self.notice.card._shadow.isWindow())

    def test_opening_another_profile_hides_the_card_at_once(self) -> None:
        self._answer("profile:gemini", ("Gemini AI",))
        self.notice.request("profile:youtube")
        self.assertFalse(self.notice.card.isVisible())
        self.assertFalse(self.notice.card._shadow.isVisible())

    def test_stale_answer_for_another_profile_is_ignored(self) -> None:
        self.notice.request("profile:youtube")
        self.notice._on_loaded(1, "profile:gemini", ("Gemini AI",))
        self.assertFalse(self.notice.card.isVisible())

    def test_closed_card_does_not_come_back_for_the_same_service(self) -> None:
        self._answer("profile:gemini", ("Gemini AI",))
        self.notice.card.close_button.click()
        self.assertFalse(self.notice.card.isVisible())

        self._answer("profile:gemini", ("Gemini AI",))
        self.assertFalse(self.notice.card.isVisible())

        self._answer("profile:chatgpt", ("ChatGPT",))
        self.assertTrue(self.notice.card.isVisible())

    def test_buttons_lead_to_hosts_and_dns(self) -> None:
        self._answer("profile:gemini", ("Gemini AI",))
        self.notice.card.hosts_button.click()
        self.notice.card.dns_button.click()
        self.open_hosts.assert_called_once_with()
        self.open_dns.assert_called_once_with()


class ProfileGeoNoticeDepsTests(unittest.TestCase):
    def _kwargs(self, state, hosts_feature):
        from app.page_names import PageName
        from ui.page_deps.presets import build_profile_setup_page_kwargs

        profile_feature = Mock()
        profile_feature.get_profile_list_file_editor_state.return_value = state
        self.show_page = Mock()
        return build_profile_setup_page_kwargs(
            page_name=PageName.ZAPRET2_PROFILE_SETUP,
            profile_feature=profile_feature,
            hosts_feature=hosts_feature,
            show_page=self.show_page,
            on_profile_setup_changed=Mock(),
        )

    def _services(self, state) -> tuple[str, ...]:
        hosts_feature = SimpleNamespace(
            load_geo_services_for_site_list=lambda text: geo_services_for_site_list(GEO, text)
        )
        worker = self._kwargs(state, hosts_feature)["create_profile_geo_services_worker"](7, "profile:gemini")
        answers: list[tuple] = []
        worker.loaded.connect(lambda *args: answers.append(args))
        worker.run()
        self.assertEqual(answers[0][:2], (7, "profile:gemini"))
        return answers[0][2]

    def test_worker_checks_hostlist_of_the_profile(self) -> None:
        state = SimpleNamespace(kind="hostlist", text="gemini.google.com\n")
        self.assertEqual(self._services(state), ("Gemini AI",))

    def test_ipset_and_missing_profile_give_no_notice(self) -> None:
        self.assertEqual(self._services(SimpleNamespace(kind="ipset", text="gemini.google.com\n")), ())
        self.assertEqual(self._services(None), ())

    def test_buttons_open_hosts_editor_and_dns_pages(self) -> None:
        from app.page_names import PageName

        kwargs = self._kwargs(None, SimpleNamespace())
        kwargs["open_hosts_editor"]()
        kwargs["open_dns_settings"]()
        self.assertEqual(
            [call.args[0] for call in self.show_page.call_args_list],
            [PageName.HOSTS, PageName.NETWORK],
        )

    def test_without_hosts_feature_page_gets_no_worker(self) -> None:
        self.assertIsNone(self._kwargs(None, None)["create_profile_geo_services_worker"])


if __name__ == "__main__":
    unittest.main()
