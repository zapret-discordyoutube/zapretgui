"""Страницы, собираемые блоками: что строится сразу, что позже и не теряются ли настройки.

Ядро блоков проверяет ``test_block_build.py``. Здесь — сами страницы:

* конструктор не собирает блоки и не достраивает их случайным обращением к
  виджету (иначе блок ничего не экономит);
* блок объявил ровно те атрибуты, которые создаёт: забытый атрибут стал бы
  ошибкой «нет такого атрибута» у пользователя, лишний — обманул бы читателя;
* настройки и состояние, пришедшие раньше блока, блок получает при сборке.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from telegram_proxy.config import settings as telegram_proxy_settings
from telegram_proxy.config.upstream_catalog import UpstreamCatalog
from telegram_proxy.ui import advanced_page as advanced_page_module
from telegram_proxy.ui import page as main_page_module
from telegram_proxy.ui.advanced_page import TelegramProxyAdvancedPage
from telegram_proxy.ui.page import TelegramProxyPage


def _feature(*, running: bool = False) -> MagicMock:
    feature = MagicMock()
    manager = feature.get_proxy_manager.return_value
    manager.is_running = running
    manager.host = "127.0.0.1"
    manager.port = 1353
    manager.upstream_state = None
    feature.has_pending_settings_saves.return_value = False
    return feature


def _built_blocks(page) -> list[str]:
    return [name for name, block in page._lazy_blocks.items() if block.is_built()]


class _PageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _assert_blocks_declare_what_they_create(self, page, declared: tuple[str, ...]) -> None:
        before = set(vars(page))
        page.ensure_all_blocks()
        created = {name for name in set(vars(page)) - before if not name.startswith("_height_of_block")}
        self.assertEqual(created, set(declared))
        self.assertEqual(page._forced_blocks, [], "блоки собраны явно, а не обращением к виджету")


class TelegramProxyMainPageBlocksTests(_PageCase):
    def _page(self, *, running: bool = False) -> TelegramProxyPage:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        return TelegramProxyPage(
            host,
            telegram_proxy_feature=_feature(running=running),
            get_zapret_running=lambda: False,
            open_advanced_settings=lambda: None,
        )

    def test_constructor_builds_only_the_status_card(self) -> None:
        page = self._page()

        self.assertEqual(_built_blocks(page), [])
        self.assertEqual(page._forced_blocks, [])
        # Кнопка запуска нужна сразу, поля настроек — нет.
        self.assertIn("_btn_toggle", vars(page))
        self.assertNotIn("_host_edit", vars(page))
        self.assertNotIn("_hosts_btn", vars(page))

    def test_blocks_declare_exactly_the_widgets_they_create(self) -> None:
        self._assert_blocks_declare_what_they_create(
            self._page(),
            main_page_module._SETTINGS_BLOCK_ATTRS + main_page_module._HOSTS_BLOCK_ATTRS,
        )

    def test_everyday_calls_do_not_build_blocks(self) -> None:
        page = self._page()

        page.set_ui_language("en")
        page.on_page_activated()
        page._on_manager_status_changed(True)
        page._on_manager_status_changed(False)
        page._apply_hosts_row()

        self.assertEqual(_built_blocks(page), [])
        self.assertEqual(page._forced_blocks, [])

    def test_settings_loaded_before_the_block_reach_it(self) -> None:
        page = self._page()
        state = replace(
            telegram_proxy_settings.default_state(),
            host="0.0.0.0",
            port=2222,
            mode="mtproxy",
            auto_deeplink=False,
        )

        page._apply_initial_settings_state(state)
        self.assertEqual(_built_blocks(page), [], "загрузка настроек блок не достраивает")
        self.assertTrue(page._initial_state_applied)

        page.ensure_block(main_page_module.SETTINGS_BLOCK)
        self.assertEqual(page._host_edit.text(), "0.0.0.0")
        self.assertEqual(page._port_spin.value(), 2222)
        self.assertFalse(page._auto_deeplink_toggle.isChecked())
        self.assertFalse(page._mtproxy_secret_row.isHidden(), "строки MTProxy видны в режиме MTProxy")

    def test_reading_a_setting_before_the_block_gives_the_loaded_value(self) -> None:
        page = self._page()
        page._apply_initial_settings_state(replace(telegram_proxy_settings.default_state(), port=3333))

        # Так порт читает кнопка «Копировать ссылку», нажатая до сборки блока.
        self.assertEqual(page._port_spin.value(), 3333)
        self.assertEqual(page._forced_blocks, [(main_page_module.SETTINGS_BLOCK, "_port_spin")])

    def test_settings_loaded_after_the_block_are_applied_at_once(self) -> None:
        page = self._page()
        page.ensure_block(main_page_module.SETTINGS_BLOCK)

        page._apply_initial_settings_state(replace(telegram_proxy_settings.default_state(), port=4444))

        self.assertEqual(page._port_spin.value(), 4444)

    def test_running_proxy_locks_address_and_port_in_a_late_block(self) -> None:
        page = self._page(running=True)

        page.ensure_block(main_page_module.SETTINGS_BLOCK)
        self.assertFalse(page._port_spin.isEnabled())
        self.assertFalse(page._host_edit.isEnabled())

        page._on_manager_status_changed(False)
        self.assertTrue(page._port_spin.isEnabled())
        self.assertTrue(page._host_edit.isEnabled())

    def test_late_block_saves_edits(self) -> None:
        page = self._page()
        feature = page._telegram_proxy
        page.ensure_block(main_page_module.SETTINGS_BLOCK)
        feature.reset_mock()

        page._port_spin.setValue(5555)

        self.assertTrue(feature.method_calls, "сигналы полей подключает сам блок")

    def test_tour_targets_build_their_blocks(self) -> None:
        page = self._page()

        self.assertIs(page.onboarding_target("hosts"), page._hosts_card)
        self.assertIs(page.onboarding_target("settings"), page._settings_card)
        self.assertEqual(sorted(_built_blocks(page)), ["hosts", "settings"])


class TelegramProxyAdvancedPageBlocksTests(_PageCase):
    def _page(self) -> TelegramProxyAdvancedPage:
        host = QWidget()
        self.addCleanup(host.deleteLater)
        return TelegramProxyAdvancedPage(
            host,
            telegram_proxy_feature=_feature(running=True),
            open_telegram_proxy=lambda: None,
        )

    @staticmethod
    def _catalog() -> UpstreamCatalog:
        return UpstreamCatalog(
            [
                {"id": "nl", "name": "Нидерланды", "type": "socks5", "host": "10.0.0.1", "port": 1080},
                {"id": "ee", "name": "Эстония", "type": "socks5", "host": "10.0.0.2", "port": 1080},
            ]
        )

    def test_constructor_builds_no_group(self) -> None:
        page = self._page()

        self.assertEqual(_built_blocks(page), [])
        self.assertEqual(page._forced_blocks, [])
        self.assertNotIn("_upstream_toggle", vars(page))

    def test_blocks_declare_exactly_the_widgets_they_create(self) -> None:
        self._assert_blocks_declare_what_they_create(
            self._page(),
            advanced_page_module._UPSTREAM_ATTRS
            + advanced_page_module._CLOUDFLARE_ATTRS
            + advanced_page_module._NETWORK_ATTRS,
        )

    def test_opening_and_loading_do_not_build_blocks(self) -> None:
        page = self._page()

        page.on_page_activated()
        page._apply_upstream_catalog(self._catalog())
        page._apply_settings_state(telegram_proxy_settings.default_state())
        page._on_upstream_state_changed(object())
        page.set_ui_language("en")

        self.assertEqual(_built_blocks(page), [])
        self.assertEqual(page._forced_blocks, [])

    def test_settings_loaded_before_the_blocks_reach_them(self) -> None:
        page = self._page()
        state = replace(
            telegram_proxy_settings.default_state(),
            upstream_enabled=True,
            upstream_preset_index=1,
            cloudflare_enabled=True,
            cloudflare_domains=("example.com",),
            pool_size=6,
        )
        page._apply_upstream_catalog(self._catalog())
        page._apply_settings_state(state)

        page.ensure_all_blocks()

        self.assertTrue(page._upstream_toggle.isChecked())
        self.assertEqual(page._upstream_preset_row.combo.currentIndex(), 1)
        self.assertGreaterEqual(page._upstream_preset_row.combo.count(), 2, "список серверов — из загруженного каталога")
        self.assertTrue(page._cloudflare_toggle.isChecked())
        self.assertEqual(page._cloudflare_domains_edit.text(), "example.com")
        self.assertFalse(page._cloudflare_domains_row.isHidden())
        self.assertEqual(page._pool_size_spin.value(), 6)

    def test_new_settings_reach_only_built_blocks(self) -> None:
        page = self._page()
        page.ensure_block(advanced_page_module.NETWORK_BLOCK)

        page._apply_settings_state(replace(telegram_proxy_settings.default_state(), pool_size=7))

        self.assertEqual(page._pool_size_spin.value(), 7)
        self.assertEqual(_built_blocks(page), [advanced_page_module.NETWORK_BLOCK])

    def test_late_block_saves_edits(self) -> None:
        page = self._page()
        feature = page._telegram_proxy
        page.ensure_block(advanced_page_module.NETWORK_BLOCK)
        feature.reset_mock()

        page._pool_size_spin.setValue(5)

        self.assertTrue(feature.method_calls, "сигналы полей подключает сам блок")

    def test_tour_targets_build_their_blocks(self) -> None:
        page = self._page()

        self.assertIs(page.onboarding_target("upstream"), page._upstream_card)
        self.assertIs(page.onboarding_target("cloudflare"), page._cloudflare_card)


class StrategyDetailsViewTests(_PageCase):
    def test_details_page_is_built_when_first_opened(self) -> None:
        from profile.ui.strategy_list.widget import ProfileStrategyListWidget

        widget = ProfileStrategyListWidget()
        self.addCleanup(widget.deleteLater)

        self.assertIsNone(widget._built_details_view, "пока подробности не открывали, их страницы нет")
        self.assertEqual(widget._pages.count(), 1)
        # Обычная жизнь списка страницу подробностей не трогает.
        widget.set_current_strategy_id("none")
        self.assertIsNone(widget.onboarding_target("details_steps"))
        widget.close_details()
        self.assertIsNone(widget._built_details_view)

        view = widget._details_view
        self.assertIs(widget._built_details_view, view)
        self.assertIs(widget._details_view, view, "страница одна на всё время жизни списка")
        self.assertEqual(widget._pages.count(), 2)
        self.assertEqual(widget._pages.currentIndex(), 0, "сборка страницы подробностей список не закрывает")


if __name__ == "__main__":
    unittest.main()
