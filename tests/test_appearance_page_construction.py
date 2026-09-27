from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.state_store import AppUiState, MainWindowStateStore
from settings import appearance as appearance_settings


_CALLBACK_NAMES = (
    "on_garland_changed",
    "on_snowflakes_changed",
    "on_background_refresh_needed",
    "on_background_preset_changed",
    "on_opacity_changed",
    "on_animations_changed",
    "on_smooth_scroll_changed",
    "on_editor_smooth_scroll_changed",
    "on_ui_language_changed",
    "on_sidebar_icon_style_changed",
)

_PREMIUM_SAVE_ACTIONS = {"background_preset", "garland_enabled", "snowflakes_enabled"}


def _clear_appearance_caches() -> None:
    for name in dir(appearance_settings):
        if name.startswith("clear_warmed_") and name.endswith("_cache"):
            getattr(appearance_settings, name)()


class AppearancePageConstructionTests(unittest.TestCase):
    """Страница строится настоящим __init__ — так ловятся ошибки построения.

    Именно такого теста не было, когда страница падала при каждом открытии
    из-за распаковки кортежа Premium-статуса.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _build_page(self, state: AppUiState, *, preset: str = "amoled", effects: bool = True):
        from ui.pages.appearance_page import AppearancePage

        plan = replace(
            appearance_settings.build_default_page_initial_state(),
            background_preset=preset,
            animations_enabled=True,
            garland_enabled=effects,
            snowflakes_enabled=effects,
        )
        _clear_appearance_caches()
        self.addCleanup(_clear_appearance_caches)
        appearance_settings.store_warmed_page_initial_state(plan)

        store = MainWindowStateStore(state)
        callbacks = {name: Mock() for name in _CALLBACK_NAMES}
        save_patch = patch.object(AppearancePage, "_request_appearance_save")
        save = save_patch.start()
        self.addCleanup(save_patch.stop)

        page = AppearancePage(
            None,
            **callbacks,
            appearance_feature=Mock(),
            ui_state_store=store,
        )
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.cleanup)
        return page, store, save, callbacks

    def _premium_saves(self, save: Mock) -> list[str]:
        return [call.args[0] for call in save.call_args_list if call.args and call.args[0] in _PREMIUM_SAVE_ACTIONS]

    def test_unknown_status_shows_last_saved_premium_appearance(self) -> None:
        page, _store, save, callbacks = self._build_page(AppUiState(subscription_known=False))

        self.assertTrue(page._bg_radio_amoled.isEnabled())
        self.assertTrue(page._bg_radio_amoled.isChecked())
        self.assertTrue(page._garland_checkbox.isEnabled())
        self.assertTrue(page._garland_checkbox.isChecked())
        self.assertTrue(page._snowflakes_checkbox.isChecked())
        self.assertEqual(self._premium_saves(save), [])
        callbacks["on_background_preset_changed"].assert_not_called()

    def test_known_free_status_locks_premium_controls_without_saving(self) -> None:
        page, _store, save, callbacks = self._build_page(
            AppUiState(subscription_known=True, subscription_is_premium=False)
        )

        self.assertFalse(page._bg_radio_amoled.isEnabled())
        self.assertFalse(page._bg_radio_rkn_chan.isEnabled())
        self.assertTrue(page._bg_radio_standard.isChecked())
        self.assertFalse(page._garland_checkbox.isEnabled())
        self.assertFalse(page._garland_checkbox.isChecked())
        self.assertFalse(page._snowflakes_checkbox.isChecked())
        self.assertFalse(page._rkn_background_combo.isEnabled())
        # Сохраняет сброс и применяет фон окно (ui/window_premium_appearance.py).
        self.assertEqual(self._premium_saves(save), [])
        callbacks["on_background_preset_changed"].assert_not_called()
        callbacks["on_garland_changed"].assert_not_called()

    def test_known_premium_status_keeps_premium_background(self) -> None:
        page, _store, _save, _callbacks = self._build_page(
            AppUiState(subscription_known=True, subscription_is_premium=True),
            preset="rkn_chan",
        )

        self.assertTrue(page._bg_radio_rkn_chan.isEnabled())
        self.assertTrue(page._bg_radio_rkn_chan.isChecked())
        self.assertTrue(page._garland_checkbox.isEnabled())

    def test_subscription_answer_after_construction_updates_controls(self) -> None:
        page, store, save, callbacks = self._build_page(AppUiState(subscription_known=False))

        store.set_subscription(False)

        self.assertFalse(page._bg_radio_amoled.isEnabled())
        self.assertTrue(page._bg_radio_standard.isChecked())
        self.assertFalse(page._garland_checkbox.isChecked())
        self.assertEqual(self._premium_saves(save), [])
        callbacks["on_background_preset_changed"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
