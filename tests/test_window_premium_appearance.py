from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from app.state_store import AppUiState, MainWindowStateStore
from settings import appearance as appearance_settings
from ui.window_premium_appearance import WindowPremiumAppearance


class WindowPremiumAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        appearance_settings.store_warmed_background_preset("amoled")
        appearance_settings.store_warmed_premium_effects(True, True)
        appearance_settings.store_warmed_animations_enabled(True)
        self.addCleanup(appearance_settings.clear_warmed_background_preset_cache)
        self.addCleanup(appearance_settings.clear_warmed_premium_effects_cache)
        self.addCleanup(appearance_settings.clear_warmed_animations_enabled_cache)

        self.window = object()
        patches = {
            "background": patch("ui.theme.apply_window_background"),
            "garland": patch("ui.window_appearance_state.apply_garland_enabled"),
            "snowflakes": patch("ui.window_appearance_state.apply_snowflakes_enabled"),
        }
        self.mocks = {name: item.start() for name, item in patches.items()}
        for item in patches.values():
            self.addCleanup(item.stop)

    def _start(self, state: AppUiState):
        store = MainWindowStateStore(state)
        create_reset_worker = Mock()
        appearance = WindowPremiumAppearance(
            window=self.window,
            ui_state_store=store,
            create_reset_worker=create_reset_worker,
        )
        appearance._start_reset = Mock()
        appearance.start()
        self.addCleanup(appearance.stop)
        return appearance, store

    def test_unknown_status_applies_saved_premium_appearance_without_reset(self) -> None:
        appearance, _store = self._start(AppUiState(subscription_known=False))

        self.mocks["background"].assert_called_once_with(self.window, preset="amoled")
        self.mocks["garland"].assert_called_with(self.window, True)
        self.mocks["snowflakes"].assert_called_with(self.window, True)
        appearance._start_reset.assert_not_called()

    def test_known_free_status_applies_standard_and_resets_saved_settings(self) -> None:
        appearance, _store = self._start(AppUiState(subscription_known=True, subscription_is_premium=False))

        self.mocks["background"].assert_called_once_with(self.window, preset="standard")
        self.mocks["garland"].assert_called_with(self.window, False)
        self.mocks["snowflakes"].assert_called_with(self.window, False)
        appearance._start_reset.assert_called_once_with()

    def test_free_answer_after_start_switches_window_to_standard(self) -> None:
        appearance, store = self._start(AppUiState(subscription_known=False))
        self.mocks["background"].reset_mock()

        store.set_subscription(False)

        self.mocks["background"].assert_called_once_with(self.window, preset="standard")
        self.mocks["garland"].assert_called_with(self.window, False)
        appearance._start_reset.assert_called_once_with()

    def test_premium_answer_keeps_background_without_reapplying(self) -> None:
        appearance, store = self._start(AppUiState(subscription_known=False))
        self.mocks["background"].reset_mock()

        store.set_subscription(True, 30)

        self.mocks["background"].assert_not_called()
        self.mocks["garland"].assert_called_with(self.window, True)
        appearance._start_reset.assert_not_called()

    def test_free_without_premium_settings_does_not_reset(self) -> None:
        appearance_settings.store_warmed_background_preset("standard")
        appearance_settings.store_warmed_premium_effects(False, False)

        appearance, _store = self._start(AppUiState(subscription_known=True, subscription_is_premium=False))

        appearance._start_reset.assert_not_called()

    def test_page_toggle_is_blocked_for_known_free_status(self) -> None:
        appearance, _store = self._start(AppUiState(subscription_known=True, subscription_is_premium=False))
        self.mocks["garland"].reset_mock()

        appearance.sync_holiday_effects(garland=True)

        self.mocks["garland"].assert_called_once_with(self.window, False)


class WindowPremiumAppearanceResetWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_reset_runs_in_background_thread_and_resyncs_effects(self) -> None:
        from settings.appearance_workers import AppearancePremiumResetWorker

        reset = Mock(return_value=appearance_settings.AppearancePremiumEffectsPlan(False, False))
        workers: list[AppearancePremiumResetWorker] = []

        def _create_worker():
            worker = AppearancePremiumResetWorker(reset_premium_appearance=reset)
            workers.append(worker)
            return worker

        appearance = WindowPremiumAppearance(
            window=object(),
            ui_state_store=MainWindowStateStore(AppUiState(subscription_known=True)),
            create_reset_worker=_create_worker,
        )
        callback_threads = []
        appearance.sync_holiday_effects = Mock(
            side_effect=lambda: callback_threads.append(QThread.currentThread())
        )

        appearance._start_reset()
        appearance._start_reset()  # второй запуск, пока первый не закончился, не нужен

        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0].wait(5000))
        QApplication.processEvents()
        reset.assert_called_once_with()
        appearance.sync_holiday_effects.assert_called_once_with()
        # Эффекты — виджеты окна: итог сброса обязан прийти в GUI-поток.
        self.assertIs(callback_threads[0], QApplication.instance().thread())

    def test_subscription_handler_logs_instead_of_breaking_store_update(self) -> None:
        store = MainWindowStateStore(AppUiState(subscription_known=False))
        appearance = WindowPremiumAppearance(window=object(), ui_state_store=store, create_reset_worker=Mock())

        with (
            patch("ui.theme.apply_window_background", side_effect=RuntimeError("wrapped C/C++ object deleted")),
            patch("ui.window_premium_appearance.log") as log,
        ):
            appearance.start()
            self.addCleanup(appearance.stop)
            self.assertTrue(store.set_subscription(True, 10))

        self.assertEqual(log.call_count, 2)
        self.assertIn("ERROR", log.call_args.args)


class AppearanceSaveFailureTests(unittest.TestCase):
    def test_failed_database_write_keeps_cache_unchanged(self) -> None:
        appearance_settings.store_warmed_premium_effects(False, False)
        self.addCleanup(appearance_settings.clear_warmed_premium_effects_cache)

        with patch("settings.store.set_garland_enabled", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                appearance_settings.save_garland_enabled(True)

        self.assertFalse(appearance_settings.peek_warmed_premium_effects().garland_enabled)

    def test_failed_reset_keeps_premium_background_in_cache(self) -> None:
        appearance_settings.store_warmed_background_preset("amoled")
        self.addCleanup(appearance_settings.clear_warmed_background_preset_cache)

        with patch("settings.store.reset_premium_appearance", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                appearance_settings.reset_premium_appearance()

        self.assertEqual(appearance_settings.peek_warmed_background_preset(), "amoled")


if __name__ == "__main__":
    unittest.main()
