"""Ссылки на вики и git в окне открываются под открытым именем сайта, а не под основным."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon


class SiteLinkButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_click_goes_through_site_links_not_straight_to_the_blocked_name(self) -> None:
        from ui.widgets.site_link_button import SiteLinkButton

        button = SiteLinkButton(FluentIcon.HELP, "https://wiki.zapret.moe/Zapret/hosts", "Справка")
        self.addCleanup(button.deleteLater)
        with patch("ui.widgets.site_link_button.site_links.open_in_browser") as opened, patch(
            "qfluentwidgets.components.widgets.button.QDesktopServices.openUrl"
        ) as direct:
            button.click()

        opened.assert_called_once_with("https://wiki.zapret.moe/Zapret/hosts")
        direct.assert_not_called()

    def test_help_link_of_every_page_is_a_site_link(self) -> None:
        # «Как это работает?» на BlockCheck, «Анализе лога» и в «Редакторе hosts» — одна функция.
        from ui.widgets.site_link_button import SiteLinkButton
        from ui.widgets.title_help_link import make_help_link

        link = make_help_link("https://wiki.zapret.moe/Zapret/Blockcheck", tooltip="t", description="d")
        self.addCleanup(link.deleteLater)
        self.assertIsInstance(link, SiteLinkButton)
        self.assertEqual(link.text(), "Как это б#&&^ь работает?")


if __name__ == "__main__":
    unittest.main()
