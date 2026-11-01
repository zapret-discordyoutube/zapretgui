"""Подсказка на странице профиля: гео-сервису нужен hosts или DNS, а не стратегия."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

from hosts.geo_sites import GeoSites, build_geo_sites, geo_services_for_site_list


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


class GeoServicesForNameTests(unittest.TestCase):
    def test_name_of_the_service_in_any_spelling(self) -> None:
        for name in ("gemini", "Gemini", "Google Gemini", "Gemini 2", "Джемини", "гемини"):
            self.assertEqual(GEO.services_for_name(name), ("Gemini AI",), name)
        for name in ("ChatGPT", "chat gpt", "чатжпт", "Чат ГПТ", "жпт", "gpt", "chatgpt.com"):
            self.assertEqual(GEO.services_for_name(name), ("ChatGPT",), name)
        self.assertEqual(GEO.services_for_name("клод"), ("Claude",))

    def test_ordinary_names_are_not_geo(self) -> None:
        for name in ("YouTube", "Discord", "Дискорд", "Telegram", "Google", "AI", "Мой профиль", "", "  "):
            self.assertEqual(GEO.services_for_name(name), (), name)

    def test_every_nickname_points_to_a_word_of_the_shipped_catalog(self) -> None:
        from hosts.geo_sites import _NAME_ALIASES, _service_name_keys
        from hosts.proxy_domains import get_services_profile_index

        index = get_services_profile_index()
        if not index.get("services"):
            self.skipTest("каталог hosts лежит в закрытом репозитории")
        geo = build_geo_sites(index)
        keys = set().union(*(_service_name_keys(name) for name in set(geo.service_by_site.values())))
        self.assertEqual(sorted({word for word in _NAME_ALIASES.values() if word not in keys}), [])
        self.assertEqual(geo.services_for_name("джемини"), ("Gemini AI",))
        self.assertTrue(geo.services_for_name("чатжпт"))
        for name in ("YouTube", "Discord", "Telegram", "Instagram", "Twitter", "Roblox", "Steam", "Мой профиль"):
            self.assertEqual(geo.services_for_name(name), (), name)


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

    def _answer(self, profile_key: str, services: tuple[str, ...], item=None) -> None:
        self.notice.request(profile_key, item)
        self.notice._on_loaded(1, profile_key, services)
        # Тестам нужна настоящая карточка, а не её снимок на время появления.
        self.notice.card.finish_appearing()

    def test_tour_example_stands_at_once_and_only_the_tour_removes_it(self) -> None:
        card = self.notice.card

        self.notice.show_example()

        # Экскурсии нужна сама карточка, а не её плавное появление.
        self.assertTrue(card.isVisible())
        self.assertFalse(card._snapshot.isVisible())
        self.assertEqual(card.title_label.text(), "Gemini: стратегия не поможет")
        # Профиль под экскурсией открылся и проверился: гео-сервисов в нём нет.
        self.notice.request("profile:youtube")
        self.notice._on_loaded(1, "profile:youtube", ())
        self.assertTrue(card.isVisible())

        self.notice.hide_example()
        self.assertFalse(card.is_shown())
        # Пример — не закрытая крестиком карточка: настоящая потом покажется.
        self._answer("profile:gemini", ("Gemini",))
        self.assertTrue(card.isVisible())

    def test_real_card_takes_the_place_of_the_example_and_outlives_the_tour(self) -> None:
        card = self.notice.card
        self.notice.show_example()

        self._answer("profile:claude", ("Claude",))
        self.assertEqual(card.title_label.text(), "Claude: стратегия не поможет")
        self.notice.hide_example()

        self.assertTrue(card.isVisible())

    def test_example_is_not_drawn_over_a_real_card(self) -> None:
        self._answer("profile:claude", ("Claude",))

        self.notice.show_example()
        self.notice.hide_example()

        self.assertEqual(self.notice.card.title_label.text(), "Claude: стратегия не поможет")
        self.assertTrue(self.notice.card.isVisible())

    def test_card_is_hidden_until_geo_service_is_found(self) -> None:
        self.assertFalse(self.notice.card.is_shown())
        self._answer("profile:youtube", ())
        self.assertFalse(self.notice.card.is_shown())

    def test_card_appears_as_a_snapshot_and_then_becomes_real(self) -> None:
        card = self.notice.card
        self.notice.request("profile:gemini")
        self.notice._on_loaded(1, "profile:gemini", ("Gemini AI",))

        # Пока карточка проявляется, на странице её снимок: настоящие кнопки
        # и тень не двигаются и не перерисовываются на каждом кадре.
        self.assertTrue(card.is_shown())
        self.assertTrue(card._snapshot.isVisible())
        self.assertFalse(card.isVisible())
        self.assertFalse(card._shadow.isVisible())
        self.assertTrue(card._snapshot.geometry().contains(card.geometry()))
        self.assertTrue(card._snapshot.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        place_before = card.pos()

        card._appear.setCurrentTime(card._appear.duration())
        self._app.processEvents()

        self.assertFalse(card._snapshot.isVisible())
        self.assertTrue(card.isVisible())
        self.assertTrue(card._shadow.isVisible())
        self.assertEqual(card.pos(), place_before)

    def test_repeated_answer_does_not_replay_the_appearance(self) -> None:
        self._answer("profile:gemini", ("Gemini AI",))
        self.notice.request("profile:gemini")
        self.notice._on_loaded(1, "profile:gemini", ("Gemini AI",))
        self.assertTrue(self.notice.card.isVisible())
        self.assertFalse(self.notice.card._snapshot.isVisible())

    def test_site_icon_replaces_the_globe(self) -> None:
        card = self.notice.card
        self.assertIs(card._icon_stack.currentWidget(), card.globe_icon)

        item = SimpleNamespace(display_name="YouTube", match_lines=("--hostlist=lists/youtube.txt",))
        self._answer("profile:youtube", ("YouTube",), item)
        self.assertIs(card._icon_stack.currentWidget(), card.site_icon)
        self.assertFalse(card.site_icon.pixmap().isNull())

        # Профиль без строки (значок неизвестен) возвращает глобус.
        self._answer("profile:other", ("Other",))
        self.assertIs(card._icon_stack.currentWidget(), card.globe_icon)

    def test_check_waits_for_the_page_and_runs_once(self) -> None:
        from profile.ui.profile_geo_notice import ProfileGeoNotice

        create_worker = Mock()
        notice = ProfileGeoNotice(self.host, create_worker=create_worker)
        notice._runtime.start_qthread_worker = Mock()

        notice.request("profile:gemini")
        notice.request("profile:gemini")
        self.assertTrue(notice._check_timer.isActive())
        notice._runtime.start_qthread_worker.assert_not_called()

        notice._check_timer.stop()
        notice._check_timer.timeout.emit()
        notice._runtime.start_qthread_worker.assert_called_once()

        notice.request("profile:gemini")
        notice.cleanup()
        self.assertFalse(notice._check_timer.isActive())

    def test_geo_profile_shows_card_in_the_bottom_right_corner(self) -> None:
        from profile.ui.profile_geo_notice import CARD_MARGIN

        self._answer("profile:gemini", ("Gemini AI",))
        card = self.notice.card

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
        self.assertFalse(self.notice.card.is_shown())
        self.assertFalse(self.notice.card._shadow.isVisible())

    def test_opening_another_profile_while_appearing_hides_the_snapshot(self) -> None:
        self.notice.request("profile:gemini")
        self.notice._on_loaded(1, "profile:gemini", ("Gemini AI",))
        self.notice.request("profile:youtube")
        self._app.processEvents()
        self.assertFalse(self.notice.card.is_shown())

    def test_stale_answer_for_another_profile_is_ignored(self) -> None:
        self.notice.request("profile:youtube")
        self.notice._on_loaded(1, "profile:gemini", ("Gemini AI",))
        self.assertFalse(self.notice.card.is_shown())

    def test_closed_card_does_not_come_back_for_the_same_service(self) -> None:
        self._answer("profile:gemini", ("Gemini AI",))
        self.notice.card.close_button.click()
        self.assertFalse(self.notice.card.isVisible())

        self._answer("profile:gemini", ("Gemini AI",))
        self.assertFalse(self.notice.card.isVisible())

        self._answer("profile:chatgpt", ("ChatGPT",))
        self.assertTrue(self.notice.card.isVisible())

    def test_ready_answer_shows_and_hides_the_card(self) -> None:
        # «Подбор стратегии» сам знает гео-сервис и отдаёт готовый ответ без фоновой проверки.
        self.notice.show_services(("Claude",), SimpleNamespace(display_name="Claude", match_lines=()))
        self.notice.card.finish_appearing()
        self.assertTrue(self.notice.card.isVisible())
        self.assertEqual(self.notice.card.title_label.text(), "Claude: стратегия не поможет")

        self.notice.show_services(())
        self.assertFalse(self.notice.card.is_shown())

    def test_typing_a_name_keeps_the_card_without_blinking(self) -> None:
        self._answer("gemini", ("Gemini AI",))
        self.notice.request("gemini ", keep_shown=True)
        self.assertTrue(self.notice.card.isVisible())
        self.notice._on_loaded(1, "gemini ", ("Gemini AI",))
        self.assertTrue(self.notice.card.isVisible())
        self.assertFalse(self.notice.card._snapshot.isVisible())

        self.notice.request("gemini x", keep_shown=True)
        self.notice._on_loaded(1, "gemini x", ())
        self.assertFalse(self.notice.card.is_shown())

    def test_profile_name_goes_to_the_check(self) -> None:
        from profile.ui.profile_geo_notice import ProfileGeoNotice

        create_worker = Mock()
        notice = ProfileGeoNotice(self.host, create_worker=create_worker)
        notice._runtime.start_qthread_worker = lambda worker_factory, **_kw: worker_factory(3)

        notice.request("user:abc", SimpleNamespace(display_name="Джемини", match_lines=()))
        notice._check_timer.stop()
        notice._check_timer.timeout.emit()

        create_worker.assert_called_once_with(3, "user:abc", "Джемини", self.host)
        notice.cleanup()

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

    _HOSTS = SimpleNamespace(
        load_geo_services_for_site_list=lambda text: geo_services_for_site_list(GEO, text),
        load_geo_services_for_name=GEO.services_for_name,
    )

    @staticmethod
    def _run(worker, key: str) -> tuple[str, ...]:
        answers: list[tuple] = []
        worker.loaded.connect(lambda *args: answers.append(args))
        worker.run()
        assert answers[0][:2] == (7, key), answers
        return answers[0][2]

    def _services(self, state, name: str = "") -> tuple[str, ...]:
        worker = self._kwargs(state, self._HOSTS)["create_profile_geo_services_worker"](7, "profile:gemini", name)
        return self._run(worker, "profile:gemini")

    def test_empty_site_list_falls_back_to_the_profile_name(self) -> None:
        state = SimpleNamespace(kind="hostlist", text="")
        self.assertEqual(self._services(state, "Джемини"), ("Gemini AI",))
        self.assertEqual(self._services(state, "YouTube"), ())
        # Список про гео-сервис важнее названия.
        listed = SimpleNamespace(kind="hostlist", text="claude.ai\n")
        self.assertEqual(self._services(listed, "Джемини"), ("Claude",))

    def test_profile_dialogs_on_both_pages_get_the_name_check(self) -> None:
        from app.page_names import PageName
        from ui.page_deps.presets import build_preset_setup_page_kwargs

        show_page = Mock()
        preset_kwargs = build_preset_setup_page_kwargs(
            page_name=PageName.ZAPRET2_PRESET_SETUP,
            profile_feature=Mock(),
            external_actions_feature=Mock(),
            open_profile_setup=Mock(),
            show_page=show_page,
            ui_state_store=None,
            hosts_feature=self._HOSTS,
        )
        profile_kwargs = self._kwargs(None, self._HOSTS)
        for kwargs in (preset_kwargs, profile_kwargs):
            worker = kwargs["create_profile_name_geo_worker"](7, "чатжпт", "чатжпт")
            self.assertEqual(self._run(worker, "чатжпт"), ("ChatGPT",))

        preset_kwargs["open_hosts_editor"]()
        preset_kwargs["open_dns_settings"]()
        self.assertEqual([call.args[0] for call in show_page.call_args_list], [PageName.HOSTS, PageName.NETWORK])
        self.assertIsNone(self._kwargs(None, None)["create_profile_name_geo_worker"])

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


class ProfileDialogGeoCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _dialog(self, name: str = ""):
        from profile.ui.user_profile_dialog import CreateUserProfileDialog

        self.parent = QWidget()
        self.parent.resize(900, 600)
        self.parent.show()
        self.addCleanup(self.parent.deleteLater)
        self.open_hosts = Mock()
        self.open_dns = Mock()
        self.checked: list[tuple[str, str]] = []

        def create_worker(request_id, key, profile_name, parent=None):
            self.checked.append((key, profile_name))
            return Mock()

        dialog = CreateUserProfileDialog(
            self.parent,
            name=name,
            create_geo_worker=create_worker,
            open_hosts_editor=self.open_hosts,
            open_dns_settings=self.open_dns,
        )
        notice = dialog._geo_notice
        notice._runtime.start_qthread_worker = lambda worker_factory, **_kw: worker_factory(1)
        notice._runtime.is_current = lambda *_args, **_kwargs: True
        return dialog, notice

    def _check(self, notice, services) -> None:
        notice._check_timer.stop()
        notice._check_timer.timeout.emit()
        notice._on_loaded(1, notice._profile_key, services)
        notice.card.finish_appearing()

    def test_typed_geo_name_shows_the_profile_page_card(self) -> None:
        dialog, notice = self._dialog()
        self.assertFalse(notice.card.is_shown())

        dialog.nameEdit.setText("Джемини")
        self._check(notice, ("Gemini AI",))

        self.assertEqual(self.checked[-1], ("Джемини", "Джемини"))
        # Та же карточка, что на странице профиля, поверх окна в правом нижнем углу.
        self.assertIs(notice.card.parentWidget(), dialog)
        # Окно в тесте не показано на экране: проверяем, что карточку не прячут.
        self.assertFalse(notice.card.isHidden())
        self.assertEqual(notice.card.title_label.text(), "Gemini AI: стратегия не поможет")

        dialog.nameEdit.clear()
        self.assertFalse(notice.card.is_shown())

    def test_edit_dialog_checks_the_current_name_at_once(self) -> None:
        _dialog, notice = self._dialog(name="ChatGPT")
        self._check(notice, ("ChatGPT",))
        self.assertFalse(notice.card.isHidden())

    def test_card_button_closes_the_dialog_and_opens_the_page(self) -> None:
        from PyQt6.QtTest import QTest

        dialog, notice = self._dialog(name="gemini")
        self._check(notice, ("Gemini AI",))
        rejected = Mock()
        dialog.rejected.connect(rejected)

        notice.card.hosts_button.click()
        QTest.qWait(300)

        rejected.assert_called_once()
        self.open_hosts.assert_called_once_with()
        self.open_dns.assert_not_called()

    def test_without_check_dialog_has_no_card(self) -> None:
        from profile.ui.user_profile_dialog import CreateUserProfileDialog

        parent = QWidget()
        parent.resize(900, 600)
        self.addCleanup(parent.deleteLater)
        dialog = CreateUserProfileDialog(parent, name="gemini")
        self.assertIsNone(dialog._geo_notice)


if __name__ == "__main__":
    unittest.main()
