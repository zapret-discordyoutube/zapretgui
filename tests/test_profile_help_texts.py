"""Кнопки «Что это?» на странице профиля: тексты, ссылки на вики и само окно."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget


class ProfileHelpTextsTests(unittest.TestCase):
    def test_every_link_is_a_wiki_article(self) -> None:
        from profile.profile_help_texts import IPSET_HELP, PROFILE_TEXT_HELP, hostlist_help

        for content in (PROFILE_TEXT_HELP, IPSET_HELP, hostlist_help()):
            with self.subTest(content=content.title):
                self.assertTrue(content.links)
                for link in content.links:
                    self.assertTrue(link.url.startswith("https://wiki.zapret.moe/"), link.url)
                    self.assertTrue(link.title and link.description)

    def test_hostlist_help_explains_priority_with_examples(self) -> None:
        from profile.profile_help_texts import hostlist_help

        text = hostlist_help().text
        for needle in ("!torrents.ru", "^example.com", "по первой записи", "сначала ваши записи",
                       "только внутри этого файла", "--hostlist-exclude", "--hostlist-auto"):
            self.assertIn(needle, text)
        self.assertNotIn("Zapret 1", text)
        self.assertIn("Zapret 1", hostlist_help(exceptions=False).text)

    def test_ipset_help_matches_engine_format(self) -> None:
        from profile.profile_help_texts import list_file_help

        text = list_file_help("ipset").text
        for needle in ("10.0.0.0/8", "с первого пакета", "--ipset-exclude", "через дефис"):
            self.assertIn(needle, text)
        self.assertEqual(list_file_help("hostlist").title, "Список сайтов")

    def test_profile_text_help_names_the_order(self) -> None:
        from profile.profile_help_texts import PROFILE_TEXT_HELP

        for needle in ("сверху вниз", "Исключение сильнее включения", "--lua-desync", "дважды"):
            self.assertIn(needle, PROFILE_TEXT_HELP.text)


class ProfileHelpButtonsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()
        self.app.closeAllWindows()
        self.app.processEvents()

    def test_help_box_has_a_button_per_article_and_opens_it(self) -> None:
        from profile.profile_help_texts import PROFILE_TEXT_HELP
        from profile.ui.profile_help_dialog import build_help_box

        host = QWidget()
        host.resize(900, 700)
        box = build_help_box(PROFILE_TEXT_HELP, host)
        self.assertEqual([b.text() for b in box.link_buttons], [link.title for link in PROFILE_TEXT_HELP.links])
        self.assertTrue(box.cancelButton.isHidden())
        with patch("utils.site_links.open_in_browser") as opened:
            box.link_buttons[2].click()
        opened.assert_called_once_with(PROFILE_TEXT_HELP.links[2].url)
        box.deleteLater()

    def test_long_help_fits_a_760px_window_and_scrolls(self) -> None:
        # Снимок с win10 (21.1.7.227, окно 1280x760): текст «Текст профиля»
        # уезжал под кнопки статей и OK, конец было не прочитать.
        from profile.profile_help_texts import PROFILE_TEXT_HELP
        from profile.ui.profile_help_dialog import build_help_box

        host = QWidget()
        host.resize(1280, 760)
        box = build_help_box(PROFILE_TEXT_HELP, host)
        box.widget.adjustSize()
        self.assertLessEqual(box.widget.sizeHint().height(), 760)
        self.assertLessEqual(box.text_area.maximumHeight(), 760 - 330)
        self.assertEqual(box.text_area.widget().text(), PROFILE_TEXT_HELP.text)
        box.deleteLater()

    def test_tabs_have_help_buttons(self) -> None:
        from profile.ui.profile_list_file_tab import USER_TOOLTIP_IPSET, ProfileListFileTab
        from profile.ui.profile_raw_text_tab import ProfileRawTextTab

        raw = ProfileRawTextTab()
        self.assertEqual(raw.help_button.text(), "Что это?")
        lists = ProfileListFileTab()
        self.assertEqual(lists.help_button.text(), "Что это?")
        lists.set_list_kind("ipset")
        self.assertEqual(lists.user_text.toolTip(), USER_TOOLTIP_IPSET)
        with patch("profile.ui.profile_list_file_tab.show_help") as shown:
            lists.help_button.click()
        self.assertEqual(shown.call_args.args[0].title, "Список адресов")
        lists.set_list_kind("hostlist")
        lists.set_exceptions_supported(False)
        with patch("profile.ui.profile_list_file_tab.show_help") as shown:
            lists.help_button.click()
        self.assertIn("Zapret 1", shown.call_args.args[0].text)


if __name__ == "__main__":
    unittest.main()
