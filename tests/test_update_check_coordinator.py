from __future__ import annotations

import unittest
import inspect
from unittest.mock import Mock, patch

from app.feature_facades.updater import UpdaterFeature
from core.runtime.update_check_coordinator import UpdateCheckCoordinator
from updater.ui.page import ServersPage
from updater.ui.update_flow import PHASE_DOWNLOADING, PHASE_FAILED, UpdateFlow


class UpdateCheckCoordinatorTests(unittest.TestCase):
    def test_page_does_not_keep_a_second_check_state(self) -> None:
        source = inspect.getsource(ServersPage)

        self.assertNotIn("self._check_state", source)
        self.assertIn("current_update_check_snapshot", source)
        self.assertIn("subscribe_update_check", source)

    def test_only_one_check_can_be_active_and_token_owns_completion(self) -> None:
        coordinator = UpdateCheckCoordinator()

        startup_token = coordinator.begin(source="startup")

        self.assertIsInstance(startup_token, int)
        self.assertIsNone(coordinator.begin(source="manual"))
        self.assertFalse(
            coordinator.finish(
                {"has_update": False, "version": "1.0"},
                source="startup",
                token=int(startup_token) + 1,
            )
        )
        self.assertEqual(coordinator.snapshot().phase, "checking")

        self.assertTrue(
            coordinator.finish(
                {"has_update": False, "version": "1.0"},
                source="startup",
                token=startup_token,
            )
        )
        self.assertEqual(coordinator.snapshot().phase, "completed")
        self.assertFalse(
            coordinator.finish(
                {"has_update": True, "version": "2.0"},
                source="startup",
                token=startup_token,
            )
        )
        self.assertFalse(coordinator.snapshot().has_update)

    def test_late_subscriber_receives_completed_startup_result(self) -> None:
        coordinator = UpdateCheckCoordinator()
        token = coordinator.begin(source="startup")
        coordinator.finish(
            {
                "has_update": True,
                "version": "2.0",
                "release_notes": "Изменения",
            },
            source="startup",
            token=token,
        )
        received = []

        coordinator.subscribe(received.append, emit_initial=True)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].phase, "completed")
        self.assertTrue(received[0].has_update)
        self.assertEqual(received[0].version, "2.0")

    def test_delayed_startup_check_does_not_repeat_completed_manual_check(self) -> None:
        coordinator = UpdateCheckCoordinator()
        manual_token = coordinator.begin(source="manual")
        coordinator.finish(
            {"has_update": False, "version": "1.0"},
            source="manual",
            token=manual_token,
        )

        self.assertIsNone(coordinator.begin(source="startup"))
        self.assertEqual(coordinator.snapshot().source, "manual")
        self.assertEqual(coordinator.snapshot().phase, "completed")

    def test_cancelled_manual_check_does_not_block_delayed_startup_check(self) -> None:
        coordinator = UpdateCheckCoordinator()
        manual_token = coordinator.begin(source="manual")
        coordinator.finish(
            {
                "has_update": False,
                "skipped": True,
                "skip_reason": "Страница закрыта",
            },
            source="manual",
            token=manual_token,
        )

        self.assertIsInstance(coordinator.begin(source="startup"), int)
        self.assertEqual(coordinator.snapshot().source, "startup")
        self.assertEqual(coordinator.snapshot().phase, "checking")

    def test_skipped_check_keeps_time_of_last_real_check(self) -> None:
        coordinator = UpdateCheckCoordinator()
        token = coordinator.begin(source="startup")

        coordinator.finish(
            {
                "has_update": False,
                "version": "1.0",
                "skipped": True,
                "skip_reason": "Лимит частоты",
                "checked_at": 123.0,
            },
            source="startup",
            token=token,
        )

        snapshot = coordinator.snapshot()
        self.assertEqual(snapshot.phase, "skipped")
        self.assertEqual(snapshot.completed_at, 123.0)
        self.assertEqual(snapshot.message, "Лимит частоты")

    def _page(self, feature: UpdaterFeature) -> ServersPage:
        """Страница без виджетов: карточка, окно и сервисы — заглушки."""
        page = ServersPage.__new__(ServersPage)
        page._ui_language = "ru"
        page._cleanup_in_progress = False
        page._updater_feature = feature
        page._install_service = Mock(is_busy=False)
        page._check_service = Mock(is_busy=False)
        page._auto_check_enabled = False
        page._idle_view_applied = False
        page._found_version = ""
        page._found_source = ""
        page._flow = UpdateFlow()
        # Страница без __init__: PyQt не свяжет сигнал с её методом напрямую.
        page._flow.changed.connect(lambda: page._on_flow_changed())
        page._update_dialog = None
        page._auto_opened_revision = 0
        page._declined_version = ""
        page._background_offered_version = ""
        page._host_window_shown = Mock(return_value=True)
        page._host_window_in_use = Mock(return_value=True)
        page._dialog_wait_timer = Mock()
        page.update_card = Mock()
        page.present_update_dialog = Mock(return_value=True)
        return page

    def _finish_startup(self, feature: UpdaterFeature, result: dict) -> None:
        token = feature.begin_update_check(source="startup")
        feature.finish_update_check(result, source="startup", token=token)

    def test_page_opened_after_startup_uses_coordinator_result(self) -> None:
        feature = UpdaterFeature()
        self._finish_startup(
            feature,
            {"has_update": False, "version": "21.1.5.5", "release_notes": "", "error": None},
        )
        page = self._page(feature)

        feature.subscribe_update_check(page._apply_check_snapshot, emit_initial=True)
        page.on_page_activated()

        page.update_card.stop_checking.assert_called_once_with(False, "21.1.5.5")
        page.update_card.show_checked_ago.assert_called_once()

    def test_open_page_receives_live_startup_progress_and_result(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        feature.subscribe_update_check(page._apply_check_snapshot)

        self._finish_startup(
            feature,
            {"has_update": False, "version": "21.1.5.5", "release_notes": "", "error": None},
        )

        page.update_card.start_checking.assert_called_once_with()
        page.update_card.stop_checking.assert_called_once_with(False, "21.1.5.5")

    def test_open_page_receives_startup_check_error(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        feature.subscribe_update_check(page._apply_check_snapshot)

        self._finish_startup(
            feature,
            {"has_update": False, "version": "", "release_notes": "", "error": "Сервер обновлений недоступен"},
        )

        page.update_card.set_error.assert_called_once_with("Сервер обновлений недоступен")
        page.update_card.stop_checking.assert_not_called()

    def test_page_uses_last_real_check_time_when_startup_check_is_skipped(self) -> None:
        feature = UpdaterFeature()
        self._finish_startup(
            feature,
            {
                "has_update": False,
                "version": "21.1.5.5",
                "skipped": True,
                "skip_reason": "Проверка недавно выполнялась",
                "checked_at": 123.0,
            },
        )
        page = self._page(feature)

        with patch("updater.ui.page.time.time", return_value=200.0):
            feature.subscribe_update_check(page._apply_check_snapshot, emit_initial=True)

        page.update_card.show_checked_ago.assert_called_once_with(77.0)

    _FOUND = {
        "has_update": True,
        "version": "21.1.5.80",
        "release_notes": "новое",
        "release_source": "Forgejo",
        "release_history": (
            {"version": "21.1.5.80", "notes": "новое", "published_at": "", "url": "https://u/80"},
            {"version": "21.1.5.79", "notes": "старое", "published_at": "", "url": "https://u/79"},
        ),
        "release_url": "https://u/80",
        "error": None,
    }

    def test_update_found_at_startup_opens_one_update_window(self) -> None:
        feature = UpdaterFeature()
        self._finish_startup(feature, dict(self._FOUND))
        page = self._page(feature)

        feature.subscribe_update_check(page._apply_check_snapshot, emit_initial=True)

        page.present_update_dialog.assert_called_once_with()
        offer = page._flow.offer
        self.assertEqual(offer.version, "21.1.5.80")
        self.assertEqual([item["version"] for item in offer.history], ["21.1.5.80", "21.1.5.79"])
        self.assertEqual(offer.url, "https://u/80")
        page.update_card.show_found_update.assert_called_once_with("21.1.5.80", "Forgejo")
        # После «Позже» окно возвращается кнопкой на карточке.
        page.update_card.set_details_action.assert_called_with("Подробнее")

    def test_same_check_result_opens_window_only_once(self) -> None:
        feature = UpdaterFeature()
        self._finish_startup(feature, dict(self._FOUND))
        page = self._page(feature)

        page._apply_check_snapshot(feature.current_update_check_snapshot())
        page._apply_check_snapshot(feature.current_update_check_snapshot())

        page.present_update_dialog.assert_called_once_with()

    def test_skipped_version_does_not_open_window_at_startup(self) -> None:
        feature = UpdaterFeature()
        self._finish_startup(feature, dict(self._FOUND, user_skipped=True))
        page = self._page(feature)

        feature.subscribe_update_check(page._apply_check_snapshot, emit_initial=True)

        page.present_update_dialog.assert_not_called()
        page.update_card.set_details_action.assert_called_with("Подробнее")

    def test_manual_check_opens_window_even_for_skipped_version(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        feature.subscribe_update_check(page._apply_check_snapshot)

        token = feature.begin_update_check(source="manual")
        feature.finish_update_check(dict(self._FOUND, user_skipped=True), source="manual", token=token)

        page.present_update_dialog.assert_called_once_with()

    def _offer_page(self) -> ServersPage:
        feature = UpdaterFeature()
        self._finish_startup(feature, dict(self._FOUND))
        page = self._page(feature)
        page._apply_check_snapshot(feature.current_update_check_snapshot())
        return page

    def test_install_starts_download_and_remembers_whats_new(self) -> None:
        page = self._offer_page()
        page._install_service.start.return_value = True
        page._updater_feature = Mock()

        with patch("updater.ui.page.run_update_setting_write", side_effect=lambda action, **_: action()):
            page._request_install_update()

        page._install_service.start.assert_called_once()
        # Окно-продолжение проверяется в test_restart_splash.
        self.assertEqual(page._install_service.start.call_args.args, ("21.1.5.80",))
        self.assertEqual(page._flow.phase, PHASE_DOWNLOADING)
        version, history = page._updater_feature.remember_whats_new.call_args.args
        self.assertEqual(version, "21.1.5.80")
        self.assertEqual(len(history), 2)
        page.update_card.show_downloading.assert_called()

    def test_skip_remembers_version(self) -> None:
        page = self._offer_page()
        page._updater_feature = Mock()

        with patch("updater.ui.page.run_update_setting_write", side_effect=lambda action, **_: action()):
            page._request_skip_update()

        page._updater_feature.set_update_skipped_version.assert_called_once_with("21.1.5.80")
        page.update_card.show_deferred.assert_called_once_with("21.1.5.80")

    def test_no_install_while_check_is_running(self) -> None:
        page = self._offer_page()
        page._check_service.is_busy = True

        page._request_install_update()

        page._install_service.start.assert_not_called()

    def test_no_check_while_installing(self) -> None:
        page = self._page(UpdaterFeature())
        page._install_service.is_busy = True

        page._request_check_updates()

        page._check_service.start.assert_not_called()

    def test_install_failure_returns_check_button(self) -> None:
        page = self._offer_page()
        page._install_service.start.return_value = True
        with patch("updater.ui.page.run_update_setting_write"):
            page._request_install_update()
        page.update_card.reset_mock()

        page._on_install_failed("Не удалось скачать обновление")

        self.assertEqual(page._flow.phase, PHASE_FAILED)
        self.assertEqual(page._flow.progress.error_text, "Не удалось скачать обновление")
        page.update_card.show_download_error.assert_called_once_with()
        page.update_card.set_check_enabled.assert_called_once_with(True)

    # ── Программа ставит находку сама ───────────────────────────────────

    def _auto_page(self, *, source: str = "startup", window_shown: bool = True, **result) -> ServersPage:
        """Страница получила итог автоматической проверки с решением «ставить»."""
        feature = UpdaterFeature()
        page = self._page(feature)
        page._install_service.start.return_value = True
        page._host_window_shown.return_value = window_shown
        # «Человек сейчас в окне программы» — в этих тестах то же, что «окно открыто».
        page._host_window_in_use.return_value = window_shown
        page._updater_feature = Mock(wraps=feature)
        token = feature.begin_update_check(source=source)
        feature.finish_update_check(
            dict(self._FOUND, auto_install=True, **result), source=source, token=token
        )
        with patch("updater.ui.page.run_update_setting_write", side_effect=lambda action, **_: action()):
            page._apply_check_snapshot(feature.current_update_check_snapshot())
        return page

    def test_update_found_by_the_app_is_installed_without_asking(self) -> None:
        for source in ("startup", "background"):
            with self.subTest(source=source):
                with patch("settings.store.add_auto_install_attempt", return_value=1) as attempt:
                    page = self._auto_page(source=source)

                page._install_service.start.assert_called_once()
                self.assertEqual(page._install_service.start.call_args.args, ("21.1.5.80",))
                self.assertEqual(page._flow.phase, PHASE_DOWNLOADING)
                page.present_update_dialog.assert_not_called()
                # Попытка идёт в счёт лимита, который останавливает петлю перезапусков.
                attempt.assert_called_once()
                self.assertEqual(attempt.call_args.args, ("21.1.5.80",))
                # Вместе с попыткой запоминается, с какой версии идёт
                # обновление: новая версия сообщит серверу, что оно дошло.
                from config.build_info import APP_VERSION

                self.assertEqual(attempt.call_args.kwargs["from_version"], APP_VERSION)

    def test_update_waiting_for_the_server_queue_is_neither_installed_nor_offered(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        self._finish_startup(feature, dict(self._FOUND, awaiting_signal=True))

        page._apply_check_snapshot(feature.current_update_check_snapshot())

        # Программа поставит версию сама, когда сервер разрешит.
        page._install_service.start.assert_not_called()
        page.present_update_dialog.assert_not_called()
        # Торопящийся откроет окно кнопкой на карточке.
        page.update_card.show_found_update.assert_called_once_with("21.1.5.80", "Forgejo")
        page.update_card.set_details_action.assert_called_with("Подробнее")

    def test_self_install_from_tray_returns_to_tray(self) -> None:
        with patch("settings.store.add_auto_install_attempt", return_value=1):
            page = self._auto_page(window_shown=False)

        kwargs = page._install_service.start.call_args.kwargs
        # Новая версия откроется там же, в трее; о ходе обновления говорит
        # маленькая карточка в углу экрана (см. test_restart_splash).
        self.assertTrue(kwargs["start_in_tray"])

    def test_self_install_with_open_window_restarts_as_a_window(self) -> None:
        with patch("settings.store.add_auto_install_attempt", return_value=1):
            page = self._auto_page(window_shown=True)

        self.assertFalse(page._install_service.start.call_args.kwargs["start_in_tray"])

    def test_manual_install_never_asks_for_tray_start(self) -> None:
        page = self._offer_page()
        page._install_service.start.return_value = True
        page._host_window_shown.return_value = False
        page._updater_feature = Mock()

        with patch("updater.ui.page.run_update_setting_write", side_effect=lambda action, **_: action()):
            page._request_install_update()

        self.assertFalse(page._install_service.start.call_args.kwargs["start_in_tray"])
        page._updater_feature.note_auto_install_attempt.assert_not_called()

    def test_manual_check_still_asks_even_when_self_install_is_allowed(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        token = feature.begin_update_check(source="manual")
        feature.finish_update_check(dict(self._FOUND, auto_install=True), source="manual", token=token)

        page._apply_check_snapshot(feature.current_update_check_snapshot())

        page._install_service.start.assert_not_called()
        page.present_update_dialog.assert_called_once_with()

    def test_skipped_version_is_not_installed_by_the_app(self) -> None:
        page = self._auto_page(user_skipped=True)

        page._install_service.start.assert_not_called()
        page.present_update_dialog.assert_not_called()

    def test_version_postponed_in_window_is_left_alone_until_restart(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        token = feature.begin_update_check(source="manual")
        feature.finish_update_check(dict(self._FOUND), source="manual", token=token)
        page._apply_check_snapshot(feature.current_update_check_snapshot())
        page._request_dismiss_update()
        page.present_update_dialog.reset_mock()

        token = feature.begin_update_check(source="background")
        feature.finish_update_check(dict(self._FOUND, auto_install=True), source="background", token=token)
        page._apply_check_snapshot(feature.current_update_check_snapshot())

        page._install_service.start.assert_not_called()
        page.present_update_dialog.assert_not_called()

    def test_background_find_without_self_install_opens_window_once_per_version(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)

        # Попытки автоустановки исчерпаны: фоновая проверка повторяется
        # каждые полчаса, а окно с предложением — нет.
        for _ in range(3):
            token = feature.begin_update_check(source="background")
            feature.finish_update_check(dict(self._FOUND), source="background", token=token)
            page._apply_check_snapshot(feature.current_update_check_snapshot())

        page._install_service.start.assert_not_called()
        page.present_update_dialog.assert_called_once_with()

    def test_window_is_offered_when_self_install_did_not_start(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        page._install_service.start.return_value = False
        self._finish_startup(feature, dict(self._FOUND, auto_install=True))

        page._apply_check_snapshot(feature.current_update_check_snapshot())

        page.present_update_dialog.assert_called_once_with()

    def test_background_check_does_not_replace_download_progress_on_card(self) -> None:
        feature = UpdaterFeature()
        page = self._page(feature)
        page._install_service.is_busy = True
        feature.subscribe_update_check(page._apply_check_snapshot)

        feature.begin_update_check(source="background")

        page.update_card.start_checking.assert_not_called()

    def test_page_cleanup_stops_services_and_unsubscribes(self) -> None:
        page = self._page(UpdaterFeature())
        unsubscribe = Mock()
        page._unsubscribe_check = unsubscribe
        page._stop_changelog_link_open_worker = Mock()

        page.cleanup()

        unsubscribe.assert_called_once_with()
        page._check_service.shutdown.assert_called_once_with()
        page._install_service.shutdown.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
