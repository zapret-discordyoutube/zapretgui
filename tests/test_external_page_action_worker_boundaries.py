from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.feature_facades.external import ExternalActionsFeature
from ui.page_composition import PAGE_DEPS_BUILDERS
from ui.page_deps import system as system_deps
from ui.pages.about_page import AboutPage
from ui.pages.support_page import SupportPage
from app.page_names import PageName
from presets.ui.control.control_page_shared import ControlPageActionMixin
from updater.ui.page import ServersPage


class ExternalPageActionWorkerBoundaryTests(unittest.TestCase):
    def test_support_and_about_use_external_action_worker_factory(self) -> None:
        feature_source = inspect.getsource(ExternalActionsFeature)
        support_source = inspect.getsource(SupportPage)
        about_source = inspect.getsource(AboutPage)
        support_deps_source = inspect.getsource(system_deps.build_support_page_kwargs)
        about_deps_source = inspect.getsource(system_deps.build_about_page_kwargs)

        self.assertIn("create_external_action_worker", feature_source)
        self.assertIn("external_actions", PAGE_DEPS_BUILDERS[PageName.SUPPORT].features)
        self.assertIn("external_actions", PAGE_DEPS_BUILDERS[PageName.ABOUT].features)
        self.assertIn("create_open_action_worker", support_deps_source)
        self.assertIn("external_actions_feature.create_external_action_worker", support_deps_source)
        self.assertIn("create_open_action_worker", about_deps_source)
        self.assertIn("external_actions_feature.create_external_action_worker", about_deps_source)
        self.assertIn("_create_support_open_action_worker", support_source)
        self.assertIn("_create_about_open_action_worker", about_source)
        self.assertNotIn("_open_discussions_action", support_source)
        self.assertNotIn("_open_telegram_action", support_source)
        self.assertNotIn("_open_discord_action", support_source)
        self.assertNotIn("_open_discussions_action", about_source)
        self.assertNotIn("_open_support_telegram_action", about_source)
        self.assertNotIn("_open_support_discord_action", about_source)
        self.assertNotIn("_open_forum_for_beginners_action", about_source)
        self.assertNotIn("_open_help_folder_action", about_source)
        self.assertNotIn("_open_telegram_news_action", about_source)
        self.assertNotIn("_open_kvn_channel_action", about_source)
        self.assertNotIn("_open_kvn_bot_action", about_source)
        self.assertNotIn("_open_kvn_bypass_action", about_source)
        self.assertNotIn("_open_kvn_github_action", about_source)
        self.assertNotIn("ui.pages.support_open_worker", support_source)
        self.assertNotIn("ui.pages.about_open_worker", about_source)

    def test_support_open_queue_state_uses_shared_ui_helper(self) -> None:
        import importlib.util

        import ui.pages.support_page as support_page

        self.assertIsNotNone(importlib.util.find_spec("ui.queued_worker_state"))
        from ui.queued_worker_state import QueuedWorkerState

        init_source = inspect.getsource(SupportPage.__init__)
        request_source = inspect.getsource(SupportPage._request_support_open_action)
        queue_source = inspect.getsource(SupportPage._queue_support_open_action)
        finished_source = inspect.getsource(SupportPage._on_support_open_action_worker_finished)
        cleanup_source = inspect.getsource(SupportPage.cleanup)

        self.assertIs(support_page.QueuedWorkerState, QueuedWorkerState)
        self.assertIn("_support_open_state = QueuedWorkerState", init_source)
        self.assertIn("_support_open_state_obj()", request_source)
        self.assertIn("_support_open_state_obj()", queue_source)
        self.assertIn("schedule_next_after_finish", finished_source)
        self.assertIn("_support_open_state_obj().reset()", cleanup_source)

    def test_about_open_queue_state_uses_shared_ui_helper(self) -> None:
        import ui.pages.about_page as about_page
        from ui.queued_worker_state import QueuedWorkerState

        init_source = inspect.getsource(AboutPage.__init__)
        request_source = inspect.getsource(AboutPage._request_about_open_action)
        queue_source = inspect.getsource(AboutPage._queue_about_open_action)
        finished_source = inspect.getsource(AboutPage._on_about_open_action_worker_finished)
        cleanup_source = inspect.getsource(AboutPage.cleanup)

        self.assertTrue(hasattr(about_page, "QueuedWorkerState"))
        self.assertIs(about_page.QueuedWorkerState, QueuedWorkerState)
        self.assertIn("_about_open_state = QueuedWorkerState", init_source)
        self.assertIn("_about_open_state_obj()", request_source)
        self.assertIn("_about_open_state_obj()", queue_source)
        self.assertIn("schedule_next_after_finish", finished_source)
        self.assertIn("_about_open_state_obj().reset()", cleanup_source)

    def test_about_forum_help_action_opens_wiki_site(self) -> None:
        from ui.page_deps.system import build_about_page_kwargs

        class _ExternalActionsFeature:
            def __init__(self) -> None:
                self.action_fn = None

            def create_external_action_worker(self, _request_id, *, action_name, action_fn, parent=None):
                self.action_fn = action_fn
                return (action_name, parent)

        opened_urls: list[str] = []
        feature = _ExternalActionsFeature()

        with patch("about.commands.webbrowser.open", side_effect=opened_urls.append):
            kwargs = build_about_page_kwargs(
                page_name=PageName.ABOUT,
                external_actions_feature=feature,
                show_page=lambda *_args, **_kwargs: None,
                ui_state_store=object(),
            )
            worker = kwargs["create_open_action_worker"](
                1,
                action_name="forum_for_beginners",
                parent=None,
            )
            result = feature.action_fn()

        self.assertEqual(worker, ("forum_for_beginners", None))
        self.assertTrue(result.ok)
        self.assertEqual(opened_urls, ["https://publish.obsidian.md/zapret/Zapret/home"])

    def test_support_open_actions_are_queued_while_worker_runs(self) -> None:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        page = SupportPage.__new__(SupportPage)
        page._support_open_runtime = _Runtime()
        page._support_open_pending = []
        page._start_support_open_action_worker = Mock()
        SupportPage._request_support_open_action(
            page,
            "telegram",
            error_key="telegram.error",
            error_default="telegram {error}",
        )
        SupportPage._request_support_open_action(
            page,
            "discord",
            error_key="discord.error",
            error_default="discord {error}",
        )

        self.assertEqual(
            page._support_open_pending,
            [
                ("telegram", "telegram.error", "telegram {error}"),
                ("discord", "discord.error", "discord {error}"),
            ],
        )
        page._start_support_open_action_worker.assert_not_called()

    def test_duplicate_support_open_action_is_queued_once(self) -> None:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        page = SupportPage.__new__(SupportPage)
        page._support_open_runtime = _Runtime()
        page._support_open_pending = []
        page._start_support_open_action_worker = Mock()
        SupportPage._request_support_open_action(
            page,
            "telegram",
            error_key="telegram.error",
            error_default="telegram {error}",
        )
        SupportPage._request_support_open_action(
            page,
            "telegram",
            error_key="telegram.error",
            error_default="telegram {error}",
        )

        self.assertEqual(
            page._support_open_pending,
            [("telegram", "telegram.error", "telegram {error}")],
        )
        page._start_support_open_action_worker.assert_not_called()

    def test_support_open_worker_finished_schedules_next_queued_action(self) -> None:
        import ui.pages.support_page as support_page

        page = SupportPage.__new__(SupportPage)
        page._support_open_pending = [
            ("telegram", "telegram.error", "telegram {error}"),
            ("discord", "discord.error", "discord {error}"),
        ]
        page._start_support_open_action_worker = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(support_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            SupportPage._on_support_open_action_worker_finished(page, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page._start_support_open_action_worker.assert_not_called()

        single_shot.call_args.args[1]()

        page._start_support_open_action_worker.assert_called_once_with(
            "telegram",
            "telegram.error",
            "telegram {error}",
        )
        self.assertEqual(
            page._support_open_pending,
            [("discord", "discord.error", "discord {error}")],
        )

    def test_stale_support_open_worker_finished_does_not_schedule_next_queued_action(self) -> None:
        import ui.pages.support_page as support_page

        page = SupportPage.__new__(SupportPage)
        page._support_open_runtime = SimpleNamespace(request_id=2)
        page._support_open_pending = [
            ("telegram", "telegram.error", "telegram {error}"),
        ]
        page._start_support_open_action_worker = Mock()
        single_shot = Mock()

        with patch.object(support_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            SupportPage._on_support_open_action_worker_finished(page, SimpleNamespace(_request_id=1))

        single_shot.assert_not_called()
        page._start_support_open_action_worker.assert_not_called()
        self.assertEqual(
            page._support_open_pending,
            [("telegram", "telegram.error", "telegram {error}")],
        )

    def test_support_open_scheduled_start_queues_next_action(self) -> None:
        import ui.pages.support_page as support_page

        page = SupportPage.__new__(SupportPage)
        page._support_open_start_scheduled = False
        page._support_open_pending = []
        page._start_support_open_action_worker = Mock()
        first = ("telegram", "telegram.error", "telegram {error}")
        second = ("discord", "discord.error", "discord {error}")
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(support_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            SupportPage._schedule_support_open_action_worker_start(page, first)
            SupportPage._schedule_support_open_action_worker_start(page, second)

        single_shot.assert_called_once()
        self.assertEqual(page._support_open_pending, [second])

        single_shot.call_args.args[1]()

        page._start_support_open_action_worker.assert_called_once_with(
            "telegram",
            "telegram.error",
            "telegram {error}",
        )
        self.assertEqual(page._support_open_pending, [second])

    def test_support_open_result_is_ignored_when_new_action_is_pending(self) -> None:
        page = SupportPage.__new__(SupportPage)
        page._support_open_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._support_open_pending = [("discord", "discord.error", "discord {error}")]
        page._show_support_open_error = Mock()

        SupportPage._on_support_open_action_finished(
            page,
            3,
            "telegram",
            SimpleNamespace(ok=False, message="old error"),
            error_key="telegram.error",
            error_default="telegram {error}",
        )

        page._show_support_open_error.assert_not_called()

    def test_support_open_error_is_ignored_when_new_action_is_pending(self) -> None:
        page = SupportPage.__new__(SupportPage)
        page._support_open_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._support_open_pending = [("discord", "discord.error", "discord {error}")]
        page._show_support_open_error = Mock()

        SupportPage._on_support_open_action_failed(
            page,
            3,
            "telegram",
            "old error",
            error_key="telegram.error",
            error_default="telegram {error}",
        )

        page._show_support_open_error.assert_not_called()

    def test_support_cleanup_stops_open_worker_without_blocking_gui(self) -> None:
        runtime = Mock()
        page = SupportPage.__new__(SupportPage)
        page._support_open_runtime = runtime
        page._support_open_pending = [("telegram", "telegram.error", "telegram {error}")]
        page._support_open_start_scheduled = True

        SupportPage.cleanup(page)

        self.assertTrue(page._cleanup_in_progress)
        self.assertFalse(page._support_open_start_scheduled)
        self.assertEqual([], page._support_open_pending)
        runtime.stop.assert_called_once_with(
            blocking=False,
            warning_prefix="Support open action worker",
        )
        runtime.cancel.assert_called_once()

    def test_about_open_actions_are_queued_while_worker_runs(self) -> None:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        page = AboutPage.__new__(AboutPage)
        page._about_open_runtime = _Runtime()
        page._about_open_pending = []
        page._start_about_open_action_worker = Mock()
        AboutPage._request_about_open_action(
            page,
            "telegram",
            error_default="telegram {error}",
        )
        AboutPage._request_about_open_action(
            page,
            "github",
            error_default="github {error}",
            raw_error_message="raw",
        )

        self.assertEqual(
            page._about_open_pending,
            [
                ("telegram", "telegram {error}", ""),
                ("github", "github {error}", "raw"),
            ],
        )
        page._start_about_open_action_worker.assert_not_called()

    def test_duplicate_about_open_action_is_queued_once(self) -> None:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        page = AboutPage.__new__(AboutPage)
        page._about_open_runtime = _Runtime()
        page._about_open_pending = []
        page._start_about_open_action_worker = Mock()
        AboutPage._request_about_open_action(
            page,
            "telegram",
            error_default="telegram {error}",
        )
        AboutPage._request_about_open_action(
            page,
            "telegram",
            error_default="telegram {error}",
        )

        self.assertEqual(page._about_open_pending, [("telegram", "telegram {error}", "")])
        page._start_about_open_action_worker.assert_not_called()

    def test_about_open_worker_finished_schedules_next_queued_action(self) -> None:
        import ui.pages.about_page as about_page

        page = AboutPage.__new__(AboutPage)
        page._cleanup_in_progress = False
        page._about_open_pending = [
            ("telegram", "telegram {error}", ""),
            ("github", "github {error}", "raw"),
        ]
        page._start_about_open_action_worker = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(about_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            AboutPage._on_about_open_action_worker_finished(page, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page._start_about_open_action_worker.assert_not_called()

        single_shot.call_args.args[1]()

        page._start_about_open_action_worker.assert_called_once_with(
            "telegram",
            "telegram {error}",
            "",
        )
        self.assertEqual(
            page._about_open_pending,
            [("github", "github {error}", "raw")],
        )

    def test_stale_about_open_worker_finished_does_not_schedule_next_queued_action(self) -> None:
        import ui.pages.about_page as about_page

        page = AboutPage.__new__(AboutPage)
        page._cleanup_in_progress = False
        page._about_open_runtime = SimpleNamespace(request_id=2)
        page._about_open_pending = [
            ("telegram", "telegram {error}", ""),
        ]
        page._start_about_open_action_worker = Mock()
        single_shot = Mock()

        with patch.object(about_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            AboutPage._on_about_open_action_worker_finished(page, SimpleNamespace(_request_id=1))

        single_shot.assert_not_called()
        page._start_about_open_action_worker.assert_not_called()
        self.assertEqual(page._about_open_pending, [("telegram", "telegram {error}", "")])

    def test_about_open_scheduled_start_queues_next_action(self) -> None:
        import ui.pages.about_page as about_page

        page = AboutPage.__new__(AboutPage)
        page._cleanup_in_progress = False
        page._about_open_start_scheduled = False
        page._about_open_pending = []
        page._start_about_open_action_worker = Mock()
        first = ("telegram", "telegram {error}", "")
        second = ("github", "github {error}", "raw")
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(about_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            AboutPage._schedule_about_open_action_worker_start(page, first)
            AboutPage._schedule_about_open_action_worker_start(page, second)

        single_shot.assert_called_once()
        self.assertEqual(page._about_open_pending, [second])

        single_shot.call_args.args[1]()

        page._start_about_open_action_worker.assert_called_once_with(
            "telegram",
            "telegram {error}",
            "",
        )
        self.assertEqual(page._about_open_pending, [second])

    def test_about_open_result_is_ignored_when_new_action_is_pending(self) -> None:
        page = AboutPage.__new__(AboutPage)
        page._cleanup_in_progress = False
        page._about_open_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._about_open_pending = [("github", "github {error}", "")]
        page._show_about_open_error = Mock()

        AboutPage._on_about_open_action_finished(
            page,
            3,
            SimpleNamespace(ok=False, message="old error"),
            error_default="telegram {error}",
            raw_error_message="",
        )

        page._show_about_open_error.assert_not_called()

    def test_about_open_error_is_ignored_when_new_action_is_pending(self) -> None:
        page = AboutPage.__new__(AboutPage)
        page._cleanup_in_progress = False
        page._about_open_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._about_open_pending = [("github", "github {error}", "")]
        page._show_about_open_error = Mock()

        AboutPage._on_about_open_action_failed(
            page,
            3,
            "old error",
            error_default="telegram {error}",
            raw_error_message="",
        )

        page._show_about_open_error.assert_not_called()

    def test_about_cleanup_stops_open_worker_without_blocking_gui(self) -> None:
        runtime = Mock()
        page = AboutPage.__new__(AboutPage)
        page._about_open_runtime = runtime
        page._about_open_pending = [("github", "github {error}", "")]
        page._about_open_start_scheduled = True
        page._pending_tab_key = "support"
        page._ui_state_unsubscribe = None
        page._ui_state_store = object()

        AboutPage.cleanup(page)

        self.assertTrue(page._cleanup_in_progress)
        self.assertFalse(page._about_open_start_scheduled)
        self.assertIsNone(page._pending_tab_key)
        self.assertEqual([], page._about_open_pending)
        self.assertIsNone(page._ui_state_store)
        runtime.stop.assert_called_once_with(
            blocking=False,
            warning_prefix="About open action worker",
        )
        runtime.cancel.assert_called_once()

    def test_control_page_external_open_cleanup_does_not_block_gui(self) -> None:
        runtime = Mock()
        page = ControlPageActionMixin()
        page._external_open_url_runtime = runtime
        page._external_open_url_pending = [("https://example.test", "Ошибка", "{error}")]
        page._external_open_url_start_scheduled = True

        ControlPageActionMixin._stop_external_open_url_worker(page)

        self.assertFalse(page._external_open_url_start_scheduled)
        self.assertEqual([], page._external_open_url_pending)
        runtime.stop.assert_called_once_with(
            blocking=False,
            warning_prefix="External open url worker",
        )
        runtime.cancel.assert_called_once()

    def test_updater_changelog_link_cleanup_does_not_block_gui(self) -> None:
        runtime = Mock()
        page = ServersPage.__new__(ServersPage)
        page._changelog_link_open_runtime = runtime
        page._changelog_link_open_pending = "https://example.test"
        page._changelog_link_open_start_scheduled = True
        page._changelog_link_open_runtime_worker = object()

        ServersPage._stop_changelog_link_open_worker(page)

        self.assertIsNone(page._changelog_link_open_pending)
        self.assertFalse(page._changelog_link_open_start_scheduled)
        self.assertIsNone(page._changelog_link_open_runtime_worker)
        runtime.stop.assert_called_once_with(
            blocking=False,
            warning_prefix="Changelog link open worker",
        )
        runtime.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
