from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class PostStartupUpdateContractTests(unittest.TestCase):
    def test_skipped_startup_update_check_clears_checking_status(self) -> None:
        from main import post_startup_update

        class UpdaterFeature:
            finished_result = None

            def is_auto_update_enabled(self) -> bool:
                return True

            def begin_update_check(self, *, source: str) -> int:
                self.begin_source = source
                return 7

            def finish_update_check(self, result: dict, *, source: str, token: int) -> bool:
                self.finished_result = dict(result)
                self.finish_source = source
                self.finish_token = token
                return True

            def run_startup_update_check(self) -> dict:
                return {
                    "has_update": False,
                    "version": "1.2.3",
                    "release_notes": "",
                    "error": None,
                    "skipped": True,
                    "skip_reason": "Следующая автоматическая проверка возможна через 5 мин",
                }

        startup_host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_interactive_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True, interactive_logged=True),
            is_alive=Mock(return_value=True),
            ensure_page=Mock(),
            show_whats_new=Mock(return_value=True),
        )
        statuses: list[str] = []

        updater_feature = UpdaterFeature()

        with (
            patch.object(post_startup_update, "bind_startup_gate", side_effect=lambda _signal, callback, **_kwargs: callback()),
            patch.object(post_startup_update, "schedule_after", side_effect=lambda _delay_ms, callback: callback()),
            patch.object(post_startup_update, "enqueue_subsystem_task", side_effect=lambda _queue, _name, target: target()),
            patch.object(post_startup_update, "log"),
        ):
            post_startup_update.install_update_check(
                startup_host,
                updater_feature=updater_feature,
                notify=Mock(),
                set_status=statuses.append,
            )

        self.assertEqual(statuses[0], "Проверка обновлений...")
        self.assertNotEqual(statuses[-1], "Проверка обновлений...")
        self.assertIn("Следующая автоматическая проверка", statuses[-1])
        self.assertEqual(updater_feature.begin_source, "startup")
        self.assertEqual(updater_feature.finish_source, "startup")
        self.assertEqual(updater_feature.finish_token, 7)
        self.assertTrue(updater_feature.finished_result["skipped"])


class _Feature:
    """Проверка при запуске возвращает ``result``; «Что нового» — ``whats_new``."""

    def __init__(self, result: dict, whats_new: tuple = ()) -> None:
        self.result = result
        self.whats_new = whats_new
        self.seen: list[str] = []
        self.ready: list[str] = []

    def is_auto_update_enabled(self) -> bool:
        return True

    def begin_update_check(self, *, source: str) -> int:
        return 1

    def finish_update_check(self, result: dict, *, source: str, token: int) -> bool:
        return True

    def run_startup_update_check(self) -> dict:
        return dict(self.result)

    def startup_whats_new(self, _version: str) -> tuple:
        return self.whats_new

    def mark_whats_new_seen(self, version: str) -> None:
        self.seen.append(version)

    def mark_update_app_ready(self, version: str) -> bool:
        self.ready.append(version)
        return True


class PostStartupUpdateWindowTests(unittest.TestCase):
    """Одно окно обновления: путь при запуске больше не спрашивает сам."""

    _FOUND = {"has_update": True, "version": "9.9.9", "release_notes": "новое", "error": None}

    def _run(self, feature: _Feature):
        from main import post_startup_update

        host = SimpleNamespace(
            startup_post_init_ready=object(),
            startup_interactive_ready=object(),
            startup_state=SimpleNamespace(post_init_ready=True, interactive_logged=True),
            is_alive=Mock(return_value=True),
            ensure_page=Mock(),
            show_whats_new=Mock(return_value=True),
        )
        with (
            patch.object(post_startup_update, "bind_startup_gate", side_effect=lambda _signal, callback, **_kwargs: callback()),
            patch.object(post_startup_update, "schedule_after", side_effect=lambda _delay_ms, callback: callback()),
            patch.object(post_startup_update, "enqueue_subsystem_task", side_effect=lambda _queue, _name, target: target()),
            patch.object(post_startup_update, "log"),
        ):
            post_startup_update.install_update_check(
                host,
                updater_feature=feature,
                notify=Mock(),
                set_status=Mock(),
            )
        return host

    def test_found_update_creates_servers_page_which_opens_the_window(self) -> None:
        from app.page_names import PageName

        host = self._run(_Feature(self._FOUND))

        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_skipped_version_does_not_create_the_page(self) -> None:
        host = self._run(_Feature(dict(self._FOUND, user_skipped=True)))

        host.ensure_page.assert_not_called()

    def test_whats_new_is_shown_once_and_marked_seen(self) -> None:
        from config.build_info import APP_VERSION

        history = ({"version": APP_VERSION, "notes": "новое"},)
        feature = _Feature({"has_update": False, "version": APP_VERSION, "error": None}, whats_new=history)

        host = self._run(feature)

        host.show_whats_new.assert_called_once_with(APP_VERSION, history)
        self.assertEqual(feature.seen, [APP_VERSION])

    def test_new_version_tells_restart_window_it_opened(self) -> None:
        from config.build_info import APP_VERSION

        feature = _Feature({"has_update": False, "version": APP_VERSION, "error": None})

        self._run(feature)

        self.assertEqual(feature.ready, [APP_VERSION])

    def test_nothing_new_shows_no_window(self) -> None:
        host = self._run(_Feature({"has_update": False, "version": "1.0", "error": None}))

        host.show_whats_new.assert_not_called()


if __name__ == "__main__":
    unittest.main()
