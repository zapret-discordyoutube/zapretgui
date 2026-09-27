"""Авто-настройка Telegram: ссылка открывается один раз, если настройка включена."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

import telegram_proxy.config.settings as telegram_proxy_settings


class _Store:
    def __init__(self, *, auto_deeplink: bool, done: bool, telegram_installed: bool = True) -> None:
        self.auto_deeplink = auto_deeplink
        self.done = done
        self.telegram_installed = telegram_installed

    def patches(self):
        return (
            patch("settings.store.get_tg_proxy_auto_deeplink", lambda: self.auto_deeplink),
            patch("settings.store.get_tg_proxy_deeplink_done", lambda: self.done),
            patch("settings.store.set_tg_proxy_deeplink_done", lambda value: setattr(self, "done", bool(value))),
            patch.object(
                telegram_proxy_settings,
                "is_telegram_link_handler_registered",
                lambda: self.telegram_installed,
            ),
        )


def _consume(store: _Store) -> bool:
    first, second, third, fourth = store.patches()
    with first, second, third, fourth:
        return telegram_proxy_settings.consume_auto_deeplink_request()


class ConsumeAutoDeeplinkRequestTests(unittest.TestCase):
    def test_disabled_setting_never_opens_link(self) -> None:
        store = _Store(auto_deeplink=False, done=False)

        self.assertFalse(_consume(store))
        self.assertFalse(store.done)

    def test_enabled_setting_opens_link_only_once(self) -> None:
        store = _Store(auto_deeplink=True, done=False)

        self.assertTrue(_consume(store))
        self.assertTrue(store.done)
        self.assertFalse(_consume(store))

    def test_without_telegram_link_is_not_opened_and_waits_for_install(self) -> None:
        # Без программы для tg:// Windows показала бы окно «выберите приложение».
        store = _Store(auto_deeplink=True, done=False, telegram_installed=False)

        self.assertFalse(_consume(store))
        self.assertFalse(store.done)

        store.telegram_installed = True
        self.assertTrue(_consume(store))

    def test_setting_is_part_of_schema_and_page_state(self) -> None:
        from settings.normalize import normalize_telegram_proxy
        from settings.schema import default_telegram_proxy

        self.assertTrue(default_telegram_proxy()["auto_deeplink"])
        self.assertTrue(normalize_telegram_proxy({})["auto_deeplink"])
        self.assertFalse(normalize_telegram_proxy({"auto_deeplink": False})["auto_deeplink"])
        state = telegram_proxy_settings._settings_state_from_data(
            {"telegram_proxy": {"auto_deeplink": False}},
            telegram_proxy_settings.UpstreamCatalog(),
        )
        self.assertFalse(state.auto_deeplink)

    def test_save_action_writes_setting(self) -> None:
        from telegram_proxy.runtime import commands

        with patch.object(telegram_proxy_settings, "set_auto_deeplink") as set_auto_deeplink:
            commands.save_settings_action("auto_deeplink", enabled=False)

        set_auto_deeplink.assert_called_once_with(False)


class MainPageAutoDeeplinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, *, running: bool):
        from telegram_proxy.ui.page import TelegramProxyPage

        feature = MagicMock()
        manager = feature.get_proxy_manager.return_value
        manager.is_running = running
        manager.host = "127.0.0.1"
        manager.port = 1353
        feature.has_pending_settings_saves.return_value = False
        host = QWidget()
        self.addCleanup(host.deleteLater)
        page = TelegramProxyPage(
            host,
            telegram_proxy_feature=feature,
            get_zapret_running=lambda: False,
            open_advanced_settings=lambda: None,
        )
        page._request_auto_deeplink_check = Mock()
        return page, manager

    def test_already_running_proxy_checks_after_settings_are_loaded(self) -> None:
        page, _manager = self._page(running=True)

        page._request_auto_deeplink_check.assert_not_called()
        page._apply_initial_settings_state(telegram_proxy_settings.default_state())

        page._request_auto_deeplink_check.assert_called_once_with()

    def test_start_transition_checks_once(self) -> None:
        page, _manager = self._page(running=False)
        page._apply_initial_settings_state(telegram_proxy_settings.default_state())
        page._request_auto_deeplink_check.assert_not_called()

        page._on_manager_status_changed(True)
        page._on_manager_status_changed(True)

        page._request_auto_deeplink_check.assert_called_once_with()

    def test_toggle_saves_setting_and_loads_saved_value(self) -> None:
        page, _manager = self._page(running=False)
        page._apply_initial_settings_state(
            replace(telegram_proxy_settings.default_state(), auto_deeplink=False)
        )

        self.assertFalse(page._auto_deeplink_toggle.isChecked())
        page._auto_deeplink_toggle._switch_button.setChecked(True)  # как клик пользователя

        page._telegram_proxy.request_settings_save.assert_called_with("auto_deeplink", enabled=True)


class CloudflareCheckWorkerLogTests(unittest.TestCase):
    def test_worker_writes_per_domain_lines_to_proxy_log(self) -> None:
        from telegram_proxy.runtime.workers import TelegramProxyCloudflareCheckWorker

        result = SimpleNamespace(
            ok=False,
            summary=lambda: "1 из 2 отвечает",
            entries=(
                SimpleNamespace(host="a.example.com", ok=True, error=""),
                SimpleNamespace(host="b.example.com", ok=False, error="timeout"),
            ),
        )
        lines = []
        worker = TelegramProxyCloudflareCheckWorker(
            3,
            kind="domain",
            domains="a.example.com, b.example.com",
            check_cloudflare_connectivity=Mock(return_value=result),
            append_log_line_fn=lines.append,
        )
        completed = []
        worker.completed.connect(lambda request_id, value: completed.append((request_id, value)))

        worker.run()

        self.assertEqual(
            lines,
            [
                "Проверяем Cloudflare-домен...",
                "Проверка Cloudflare: 1 из 2 отвечает",
                "Cloudflare OK: a.example.com",
                "Cloudflare FAIL: b.example.com - timeout",
            ],
        )
        self.assertEqual(completed, [(3, result)])


if __name__ == "__main__":
    unittest.main()
