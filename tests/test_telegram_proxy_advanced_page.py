from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from app.page_names import PageName
from telegram_proxy.config.upstream_catalog import UpstreamCatalog
from telegram_proxy.proxy.upstream_controller import UpstreamRuntimeSnapshot


def _feature(*, pending: bool = False, running: bool = True) -> MagicMock:
    feature = MagicMock()
    feature.has_pending_settings_saves.return_value = pending
    feature.get_proxy_manager.return_value.upstream_state = None
    feature.get_proxy_manager.return_value.is_running = running
    return feature


def _fallback_snapshot() -> UpstreamRuntimeSnapshot:
    return UpstreamRuntimeSnapshot(
        selected_preset_id="nl",
        selected_name="Нидерланды",
        active_preset_id="ee",
        active_name="Эстония",
        state="fallback",
        generation=1,
    )


def _catalog() -> UpstreamCatalog:
    return UpstreamCatalog(
        [
            {"id": "nl", "name": "Нидерланды", "type": "socks5", "host": "10.0.0.1", "port": 1080},
            {"id": "ee", "name": "Эстония", "type": "socks5", "host": "10.0.0.2", "port": 1080},
        ]
    )


class TelegramProxyAdvancedRouteTests(unittest.TestCase):
    def test_route_is_hidden_nested_page_under_telegram_proxy(self) -> None:
        from ui.navigation.schema import INNER_PAGE_NAMES, PAGE_CLEANUP_ORDER, get_page_spec

        spec = get_page_spec(PageName.TELEGRAM_PROXY_ADVANCED)

        self.assertEqual(spec.module_name, "telegram_proxy.ui.advanced_page")
        self.assertEqual(spec.class_name, "TelegramProxyAdvancedPage")
        self.assertFalse(spec.is_top_level)
        self.assertTrue(spec.is_hidden)
        self.assertIsNone(spec.sidebar_group)
        self.assertEqual(spec.breadcrumb_parent, PageName.TELEGRAM_PROXY)
        self.assertNotIn(PageName.TELEGRAM_PROXY_ADVANCED, INNER_PAGE_NAMES)
        # Вложенная страница закрывается раньше основной.
        self.assertLess(
            PAGE_CLEANUP_ORDER.index(PageName.TELEGRAM_PROXY_ADVANCED),
            PAGE_CLEANUP_ORDER.index(PageName.TELEGRAM_PROXY),
        )

    def test_page_deps_open_each_other(self) -> None:
        from ui.page_composition import PAGE_DEPS_BUILDERS
        from ui.page_deps.system import (
            build_telegram_proxy_advanced_page_kwargs,
            build_telegram_proxy_page_kwargs,
        )

        self.assertIn(PageName.TELEGRAM_PROXY_ADVANCED, PAGE_DEPS_BUILDERS)
        show_page = Mock()
        feature = Mock()

        advanced_kwargs = build_telegram_proxy_advanced_page_kwargs(
            page_name=PageName.TELEGRAM_PROXY_ADVANCED,
            telegram_proxy_feature=feature,
            show_page=show_page,
        )
        main_kwargs = build_telegram_proxy_page_kwargs(
            page_name=PageName.TELEGRAM_PROXY,
            runtime_feature=Mock(),
            telegram_proxy_feature=feature,
            show_page=show_page,
        )

        self.assertIs(advanced_kwargs["telegram_proxy_feature"], feature)
        advanced_kwargs["open_telegram_proxy"]()
        show_page.assert_called_once_with(PageName.TELEGRAM_PROXY)
        show_page.reset_mock()
        main_kwargs["open_advanced_settings"]()
        show_page.assert_called_once_with(PageName.TELEGRAM_PROXY_ADVANCED, allow_internal=True)

    def test_not_in_search_index(self) -> None:
        from app.search_index import SEARCH_ENTRIES

        self.assertFalse(any(entry.page_name == PageName.TELEGRAM_PROXY_ADVANCED for entry in SEARCH_ENTRIES))


class TelegramProxyAdvancedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self, feature=None, open_telegram_proxy=None):
        from telegram_proxy.ui.advanced_page import TelegramProxyAdvancedPage

        host = QWidget()
        self.addCleanup(host.deleteLater)
        return TelegramProxyAdvancedPage(
            host,
            telegram_proxy_feature=feature or _feature(),
            open_telegram_proxy=open_telegram_proxy or (lambda: None),
        )

    def test_reload_waits_for_pending_saves(self) -> None:
        feature = _feature(pending=True)
        page = self._page(feature)
        flushed_listener = feature.add_settings_flushed_listener.call_args.args[0]

        page.on_page_activated()

        feature.create_page_initial_state_worker.assert_not_called()
        self.assertTrue(page._reload_after_flush)

        feature.has_pending_settings_saves.return_value = False
        flushed_listener("")

        feature.create_page_initial_state_worker.assert_called_once()
        self.assertFalse(page._reload_after_flush)

    def test_result_arriving_while_saves_pending_is_dropped_and_rerequested(self) -> None:
        feature = _feature()
        page = self._page(feature)
        page._apply_settings_state = Mock()
        page._state_runtime.request_id = 5
        feature.has_pending_settings_saves.return_value = True

        page._on_state_loaded(5, Mock())

        page._apply_settings_state.assert_not_called()
        self.assertTrue(page._reload_after_flush)

    def test_runtime_state_is_shown_in_server_row_description(self) -> None:
        page = self._page()
        page._apply_upstream_catalog(_catalog())
        page._upstream_toggle.setChecked(True, block_signals=True)
        page._apply_upstream_preset_ui(0)

        page._on_upstream_state_changed(
            UpstreamRuntimeSnapshot(
                selected_preset_id="nl",
                selected_name="Нидерланды",
                active_preset_id="ee",
                active_name="Эстония",
                state="fallback",
                generation=1,
            )
        )

        self.assertEqual(
            page._upstream_preset_row.contentLabel.text(),
            "Выбрано: Нидерланды · Сейчас используется: Эстония (резерв)",
        )
        self.assertFalse(page._upstream_preset_row.isHidden())

    def test_stale_runtime_state_is_not_shown(self) -> None:
        feature = _feature(running=True)
        page = self._page(feature)
        page._apply_upstream_catalog(_catalog())
        page._upstream_toggle.setChecked(True, block_signals=True)
        page._apply_upstream_preset_ui(0)
        page._on_upstream_state_changed(_fallback_snapshot())
        default = "Выберите сервер из списка или переключитесь на ручной ввод"

        # Пользователь выбрал другой сервер: старый «Сейчас используется» уже неверен.
        page._upstream_preset_row.combo.setCurrentIndex(1)
        self.assertEqual(page._upstream_preset_row.contentLabel.text(), default)

        # Прокси остановлен: прошлый снимок не показываем.
        page._on_upstream_state_changed(_fallback_snapshot())
        feature.get_proxy_manager.return_value.is_running = False
        page._refresh_upstream_preset_description()
        self.assertEqual(page._upstream_preset_row.contentLabel.text(), default)

    def test_missing_catalog_hint_is_server_row_description(self) -> None:
        page = self._page()
        page._apply_upstream_catalog(UpstreamCatalog())
        page._upstream_toggle.setChecked(True, block_signals=True)
        page._apply_upstream_preset_ui(0)

        self.assertFalse(page._upstream_preset_row.isHidden())
        self.assertIn("Доступен только ручной ввод", page._upstream_preset_row.contentLabel.text())
        self.assertFalse(page._upstream_address_row.isHidden())

    def test_edits_are_saved_through_facade(self) -> None:
        feature = _feature()
        page = self._page(feature)

        page._pool_size_spin.setValue(6)
        page._cloudflare_toggle._switch_button.setChecked(True)  # как клик пользователя

        feature.request_settings_save.assert_any_call("pool_size", value=6, restart="schedule")
        feature.request_settings_save.assert_any_call("cloudflare_enabled", enabled=True, restart="now")
        self.assertFalse(page._cloudflare_domains_row.isHidden())

    def test_breadcrumb_root_returns_to_telegram_proxy(self) -> None:
        open_telegram_proxy = Mock()
        page = self._page(open_telegram_proxy=open_telegram_proxy)

        page._breadcrumb.setCurrentItem("telegram_proxy")

        open_telegram_proxy.assert_called_once_with()
        self.assertEqual(len(page._breadcrumb.items), 2)

    def test_cleanup_unsubscribes_without_stopping_proxy(self) -> None:
        feature = _feature()
        page = self._page(feature)
        manager = feature.get_proxy_manager.return_value

        page.cleanup()

        feature.remove_settings_flushed_listener.assert_called_once_with(page._on_settings_flushed)
        manager.upstream_state_changed.disconnect.assert_called_once_with(page._on_upstream_state_changed)
        manager.cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
