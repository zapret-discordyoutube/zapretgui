from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from telegram_proxy.config import settings as telegram_proxy_settings
from telegram_proxy.ui.page import TelegramProxyPage


def _feature(*, running: bool = False) -> MagicMock:
    feature = MagicMock()
    manager = feature.get_proxy_manager.return_value
    manager.is_running = running
    manager.host = "127.0.0.1"
    manager.port = 1353
    feature.has_pending_settings_saves.return_value = False
    return feature


class TelegramProxyMainPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, *, feature=None, open_advanced_settings=None) -> TelegramProxyPage:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        return TelegramProxyPage(
            host,
            telegram_proxy_feature=feature or _feature(),
            get_zapret_running=lambda: False,
            open_advanced_settings=open_advanced_settings or (lambda: None),
        )

    def _mtproxy_rows_hidden(self, page: TelegramProxyPage) -> list[bool]:
        return [
            page._mtproxy_secret_row.isHidden(),
            page._fake_tls_domain_row.isHidden(),
            page._proxy_protocol_toggle.isHidden(),
        ]

    def test_mtproxy_rows_visible_only_in_mtproxy_mode(self) -> None:
        page = self._page()

        page._apply_initial_settings_state(replace(telegram_proxy_settings.default_state(), mode="socks5"))
        self.assertEqual(self._mtproxy_rows_hidden(page), [True, True, True])

        page._proxy_mode_row.setCurrentData("mtproxy")
        self.assertEqual(self._mtproxy_rows_hidden(page), [False, False, False])

        page._proxy_mode_row.setCurrentData("socks5")
        self.assertEqual(self._mtproxy_rows_hidden(page), [True, True, True])

    def test_saved_mtproxy_mode_shows_mtproxy_rows_after_load(self) -> None:
        page = self._page()

        page._apply_initial_settings_state(
            replace(
                telegram_proxy_settings.default_state(),
                mode="mtproxy",
                mtproxy_secret="63dae4ef747d6b64b652ead084cbcad7",
            )
        )

        self.assertEqual(self._mtproxy_rows_hidden(page), [False, False, False])
        self.assertEqual(page._mtproxy_secret_edit.text(), "63dae4ef747d6b64b652ead084cbcad7")

    def test_advanced_settings_row_opens_nested_page(self) -> None:
        open_advanced_settings = MagicMock()
        page = self._page(open_advanced_settings=open_advanced_settings)

        page._advanced_nav_btn.click()

        open_advanced_settings.assert_called_once_with()

    def test_status_and_connect_actions_share_one_card(self) -> None:
        page = self._page()
        card = page._status_card

        for widget in (
            page._status_label,
            page._btn_toggle,
            page._stats_label,
            page._setup_open_btn,
            page._setup_copy_btn,
            page._setup_zastogram_btn,
        ):
            self.assertTrue(card.isAncestorOf(widget))

    def test_host_and_port_live_in_one_setting_row(self) -> None:
        page = self._page()

        self.assertTrue(page._host_port_row.isAncestorOf(page._host_edit))
        self.assertTrue(page._host_port_row.isAncestorOf(page._port_spin))
        self.assertTrue(page._settings_card.isAncestorOf(page._host_port_row))

    def test_no_advanced_mode_toggle_on_main_page(self) -> None:
        page = self._page()

        self.assertFalse(hasattr(page, "_advanced_toggle"))
        self.assertFalse(hasattr(page, "_advanced_card"))


if __name__ == "__main__":
    unittest.main()
