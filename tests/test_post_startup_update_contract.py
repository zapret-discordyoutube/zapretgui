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
                idle_tasks=_ImmediateIdleTasks(),
            )

        self.assertEqual(statuses[0], "Проверка обновлений...")
        self.assertNotEqual(statuses[-1], "Проверка обновлений...")
        self.assertIn("Следующая автоматическая проверка", statuses[-1])
        self.assertEqual(updater_feature.begin_source, "startup")
        self.assertEqual(updater_feature.finish_source, "startup")
        self.assertEqual(updater_feature.finish_token, 7)
        self.assertTrue(updater_feature.finished_result["skipped"])


class _ImmediateIdleTasks:
    """Очередь пауз пользователя, которая в тесте выполняет задачу сразу."""

    def add(self, _name, callback, *, delay_ms=0, needs_shown_window=True) -> None:
        callback()


class _RecordingIdleTasks:
    """Очередь пауз пользователя, которая только записывает задачи."""

    def __init__(self) -> None:
        self.tasks: list[tuple[str, object, bool]] = []

    def add(self, name, callback, *, delay_ms=0, needs_shown_window=True) -> None:
        self.tasks.append((name, callback, bool(needs_shown_window)))


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

    def _run(self, feature: _Feature, *, idle_tasks=None, queued_tasks=None):
        from main import post_startup_update

        def _enqueue(queue, name, target):
            if queued_tasks is not None:
                queued_tasks.append((queue, name))
            target()

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
            patch.object(post_startup_update, "enqueue_subsystem_task", side_effect=_enqueue),
            patch.object(post_startup_update, "log"),
        ):
            post_startup_update.install_update_check(
                host,
                updater_feature=feature,
                notify=Mock(),
                set_status=Mock(),
                idle_tasks=idle_tasks or _ImmediateIdleTasks(),
            )
        return host

    def test_found_update_creates_servers_page_which_opens_the_window(self) -> None:
        from app.page_names import PageName

        host = self._run(_Feature(self._FOUND))

        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_update_page_is_built_in_user_pause_not_in_the_result_handler(self) -> None:
        from app.page_names import PageName

        idle_tasks = _RecordingIdleTasks()

        host = self._run(_Feature(self._FOUND), idle_tasks=idle_tasks)

        # Сборка страницы занимает GUI-поток: посреди клика пользователя она
        # давала рывок, поэтому ждёт паузы. При окне в трее ждать нечего.
        host.ensure_page.assert_not_called()
        self.assertEqual(
            [(name, needs_window) for name, _cb, needs_window in idle_tasks.tasks],
            [("UpdateWindowPage", False)],
        )

        idle_tasks.tasks[0][1]()
        host.ensure_page.assert_called_once_with(PageName.SERVERS)

    def test_update_page_is_not_built_after_app_started_closing(self) -> None:
        idle_tasks = _RecordingIdleTasks()
        host = self._run(_Feature(self._FOUND), idle_tasks=idle_tasks)

        host.is_alive.return_value = False
        idle_tasks.tasks[0][1]()

        host.ensure_page.assert_not_called()

    def test_interrupted_update_is_checked_in_background_queue(self) -> None:
        queued: list[tuple[str, str]] = []

        self._run(_Feature({"has_update": False, "version": "1.0", "error": None}), queued_tasks=queued)

        # Первый импорт updater.install и чтение его файлов в GUI-потоке
        # задерживали кадр сразу после появления окна.
        self.assertIn(("update", "InterruptedUpdateCheck"), queued)

    def test_whats_new_dialog_waits_for_user_pause_and_shown_window(self) -> None:
        from config.build_info import APP_VERSION

        history = ({"version": APP_VERSION, "notes": "новое"},)
        feature = _Feature({"has_update": False, "version": APP_VERSION, "error": None}, whats_new=history)
        idle_tasks = _RecordingIdleTasks()

        host = self._run(feature, idle_tasks=idle_tasks)

        # Окно строится в GUI-потоке и раньше появлялось по таймеру — посреди
        # действий пользователя и вместе со значком в трее.
        host.show_whats_new.assert_not_called()
        self.assertEqual(
            [(name, needs_window) for name, _cb, needs_window in idle_tasks.tasks],
            [("WhatsNewDialog", True)],
        )

        idle_tasks.tasks[0][1]()
        host.show_whats_new.assert_called_once_with(APP_VERSION, history)

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
