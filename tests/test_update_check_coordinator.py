from __future__ import annotations

import unittest
import inspect
from unittest.mock import Mock, patch

from app.feature_facades.updater import UpdaterFeature
from core.runtime.update_check_coordinator import UpdateCheckCoordinator
from updater.ui.page import ServersPage


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
        """Страница без виджетов: карточки и сервисы — заглушки."""
        page = ServersPage.__new__(ServersPage)
        page._ui_language = "ru"
        page._cleanup_in_progress = False
        page._updater_feature = feature
        page._install_service = Mock(is_busy=False)
        page._check_service = Mock(is_busy=False)
        page._auto_check_enabled = False
        page._idle_view_applied = False
        page._found_version = ""
        page._found_notes = ""
        page._found_source = ""
        page.update_card = Mock()
        page.changelog_card = Mock()
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

    def test_update_found_at_startup_offers_install_on_page_after_later(self) -> None:
        """Раньше после «Позже» на странице не было кнопки установки."""
        feature = UpdaterFeature()
        self._finish_startup(
            feature,
            {
                "has_update": True,
                "version": "21.1.5.80",
                "release_notes": "новое",
                "release_source": "Forgejo",
                "error": None,
            },
        )
        page = self._page(feature)

        feature.subscribe_update_check(page._apply_check_snapshot, emit_initial=True)

        page.changelog_card.show_update.assert_called_once_with("21.1.5.80", "новое")
        # Источник выпуска теперь доходит до карточки.
        page.update_card.show_found_update.assert_called_once_with("21.1.5.80", "Forgejo")

    def test_confirmed_startup_update_installs_immediately_without_timer(self) -> None:
        page = self._page(UpdaterFeature())
        page._install_service.start.return_value = True

        self.assertTrue(page.present_startup_update("21.1.5.80", "новое", install_after_show=True))

        page._install_service.start.assert_called_once_with("21.1.5.80")
        page.changelog_card.start_download.assert_called_once_with("21.1.5.80")

    def test_no_install_while_check_is_running(self) -> None:
        page = self._page(UpdaterFeature())
        page._check_service.is_busy = True
        page._found_version = "21.1.5.80"

        page._request_install_update()

        page._install_service.start.assert_not_called()

    def test_no_check_while_installing(self) -> None:
        page = self._page(UpdaterFeature())
        page._install_service.is_busy = True

        page._request_check_updates()

        page._check_service.start.assert_not_called()

    def test_install_failure_returns_check_button(self) -> None:
        page = self._page(UpdaterFeature())

        page._on_install_failed("Не удалось скачать обновление")

        page.changelog_card.download_failed.assert_called_once_with("Не удалось скачать обновление")
        page.update_card.set_check_enabled.assert_called_once_with(True)

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
