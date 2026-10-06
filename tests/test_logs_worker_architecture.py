from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.feature_facades.logs import LogsFeature
import log.ui.page as logs_page
import log.open_folder_worker as open_folder_worker
import log.runtime_workflow as log_runtime_workflow
import log.support_worker as support_worker


class _FakeLogText:
    """Поле журнала без Qt: запоминает, какими кусками в него вставляли текст."""

    def __init__(self):
        self.inserted = []
        self.MoveOperation = SimpleNamespace(End="end")

    def verticalScrollBar(self):  # noqa: N802
        return SimpleNamespace(value=lambda: 10, maximum=lambda: 10, setValue=lambda _value: None)

    def setUpdatesEnabled(self, _enabled):  # noqa: N802
        return None

    def textCursor(self):  # noqa: N802
        return self

    def movePosition(self, _operation):  # noqa: N802
        return True

    def insertText(self, text):  # noqa: N802
        self.inserted.append(text)

    def setTextCursor(self, _cursor):  # noqa: N802
        return None


class LogsWorkerArchitectureTests(unittest.TestCase):
    def test_logs_workers_receive_feature_actions_not_feature_object(self) -> None:
        feature_source = inspect.getsource(LogsFeature)
        worker_source = "\n".join(
            (
                inspect.getsource(open_folder_worker.LogsOpenFolderWorker),
                inspect.getsource(support_worker.LogsSupportPrepareWorker),
            )
        )

        self.assertNotIn("logs_feature=self", feature_source)
        self.assertNotIn("self._logs", worker_source)
        self.assertIn("open_logs_folder=self.open_logs_folder", feature_source)
        self.assertIn("open_logs_folder", inspect.signature(open_folder_worker.LogsOpenFolderWorker.__init__).parameters)
        self.assertIn("self._open_logs_folder", inspect.getsource(open_folder_worker.LogsOpenFolderWorker.run))
        self.assertNotIn("import log.commands", inspect.getsource(open_folder_worker.LogsOpenFolderWorker.run))
        self.assertIn("prepare_support_bundle=self.prepare_support_bundle", feature_source)
        self.assertIn("_prepare_support_bundle", worker_source)
        self.assertNotIn("log_commands.prepare_support_bundle", worker_source)
        self.assertNotIn("import log.commands", inspect.getsource(support_worker.LogsSupportPrepareWorker.run))

    def test_open_folder_pending_restarts_after_event_loop_turn(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._open_folder_pending = True
        page._request_open_logs_folder = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_open_logs_folder_worker_finished(page, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page._request_open_logs_folder.assert_not_called()

        single_shot.call_args.args[1]()

        page._request_open_logs_folder.assert_called_once_with()

    def test_stale_open_folder_worker_finished_does_not_restart_pending_open(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._open_folder_runtime = SimpleNamespace(request_id=2)
        page._open_folder_pending = True
        page._request_open_logs_folder = Mock()
        single_shot = Mock()

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_open_logs_folder_worker_finished(page, SimpleNamespace(_request_id=1))

        single_shot.assert_not_called()
        page._request_open_logs_folder.assert_not_called()
        self.assertTrue(page._open_folder_pending)

    def test_stale_open_folder_worker_object_finished_does_not_restart_pending_open(self) -> None:
        old_worker = object()
        current_worker = object()
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._open_folder_runtime = SimpleNamespace(request_id=2, worker=current_worker)
        page._open_folder_pending = True
        page._request_open_logs_folder = Mock()
        single_shot = Mock()

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_open_logs_folder_worker_finished(page, old_worker)

        single_shot.assert_not_called()
        page._request_open_logs_folder.assert_not_called()
        self.assertTrue(page._open_folder_pending)

    def test_open_folder_scheduled_start_is_not_duplicated(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._open_folder_pending = False
        page._open_folder_start_scheduled = False
        page._request_open_logs_folder = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._schedule_open_logs_folder_start(page)
            logs_page.LogsPage._schedule_open_logs_folder_start(page)

        single_shot.assert_called_once()
        self.assertFalse(page._open_folder_pending)
        page._request_open_logs_folder.assert_not_called()

        single_shot.call_args.args[1]()

        page._request_open_logs_folder.assert_called_once_with()

    def test_open_folder_state_uses_shared_latest_value_helper(self) -> None:
        from ui.latest_value_worker_state import LatestValueWorkerState

        init_source = inspect.getsource(logs_page.LogsPage.__init__)
        request_source = inspect.getsource(logs_page.LogsPage._request_open_logs_folder)
        finished_source = inspect.getsource(logs_page.LogsPage._on_open_logs_folder_worker_finished)
        cleanup_source = inspect.getsource(logs_page.LogsPage.cleanup)

        self.assertTrue(hasattr(logs_page, "LatestValueWorkerState"))
        self.assertIs(logs_page.LatestValueWorkerState, LatestValueWorkerState)
        self.assertIn("_open_folder_state = LatestValueWorkerState", init_source)
        self.assertIn("_open_folder_state_obj()", request_source)
        self.assertIn("schedule_pending_after_finish", finished_source)
        self.assertIn("_open_folder_state_obj().reset()", cleanup_source)

    def test_logs_overview_pending_cleanup_restarts_after_event_loop_turn(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._logs_overview_pending_cleanup = True
        page._logs_overview_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._refresh_logs_list = Mock()
        page._stop_refresh_animation = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_logs_overview_finished(page, 7, object())

        self.assertEqual(single_shot.call_count, 2)
        self.assertEqual(single_shot.call_args_list[0].args[0], 500)
        self.assertEqual(single_shot.call_args_list[1].args[0], 0)
        page._refresh_logs_list.assert_not_called()

        single_shot.call_args_list[1].args[1]()

        page._refresh_logs_list.assert_called_once_with(run_cleanup=True)

    def test_logs_overview_finish_during_cleanup_does_not_schedule_ui_updates(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = True
        page._logs_overview_pending_cleanup = True
        page._logs_overview_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._refresh_logs_list = Mock()
        page._stop_refresh_animation = Mock()
        single_shot = Mock()

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_logs_overview_finished(page, 7, object())

        single_shot.assert_not_called()
        page._refresh_logs_list.assert_not_called()
        page._stop_refresh_animation.assert_not_called()

    def test_stop_logs_overview_worker_cancels_runtime_after_stop_request(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._logs_overview_runtime = Mock()

        logs_page.LogsPage._stop_logs_overview_worker(page, blocking=False)

        page._logs_overview_runtime.stop.assert_called_once_with(
            blocking=False,
            log_fn=logs_page.log,
            warning_prefix="Logs overview worker",
        )
        page._logs_overview_runtime.cancel.assert_called_once()

    def test_stop_log_source_cancels_reader_and_closes_live_bridge(self) -> None:
        reader_runtime = Mock()
        live_bridge = Mock()

        log_runtime_workflow.stop_log_source(
            live_bridge=live_bridge,
            reader_runtime=reader_runtime,
            blocking=False,
            log_fn=logs_page.log,
            warning_prefix="Log file reader",
        )

        live_bridge.close.assert_called_once_with()
        reader_runtime.stop.assert_called_once_with(
            blocking=False,
            log_fn=logs_page.log,
            warning_prefix="Log file reader",
        )
        reader_runtime.cancel.assert_called_once()

    def test_support_prepare_pending_restarts_after_event_loop_turn(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._support_prepare_pending = True
        page._request_support_prepare = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_support_prepare_worker_finished(page, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page._request_support_prepare.assert_not_called()

        single_shot.call_args.args[1]()

        page._request_support_prepare.assert_called_once_with()

    def test_support_prepare_state_uses_shared_latest_value_helper(self) -> None:
        from ui.latest_value_worker_state import LatestValueWorkerState

        init_source = inspect.getsource(logs_page.LogsPage.__init__)
        request_source = inspect.getsource(logs_page.LogsPage._request_support_prepare)
        finished_source = inspect.getsource(logs_page.LogsPage._on_support_prepare_worker_finished)
        cleanup_source = inspect.getsource(logs_page.LogsPage._stop_support_prepare_worker)

        self.assertTrue(hasattr(logs_page, "LatestValueWorkerState"))
        self.assertIs(logs_page.LatestValueWorkerState, LatestValueWorkerState)
        self.assertIn("_support_prepare_state = LatestValueWorkerState", init_source)
        self.assertIn("_support_prepare_state_obj()", request_source)
        self.assertIn("schedule_pending_after_finish", finished_source)
        self.assertIn("_support_prepare_state_obj().reset()", cleanup_source)

    def test_stale_support_prepare_worker_finished_does_not_restart_pending_prepare(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._support_prepare_runtime = SimpleNamespace(request_id=2)
        page._support_prepare_pending = True
        page._request_support_prepare = Mock()
        single_shot = Mock()

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_support_prepare_worker_finished(page, SimpleNamespace(_request_id=1))

        single_shot.assert_not_called()
        page._request_support_prepare.assert_not_called()
        self.assertTrue(page._support_prepare_pending)

    def test_stale_support_prepare_worker_object_finished_does_not_restart_pending_prepare(self) -> None:
        old_worker = object()
        current_worker = object()
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._support_prepare_runtime = SimpleNamespace(request_id=2, worker=current_worker)
        page._support_prepare_pending = True
        page._request_support_prepare = Mock()
        single_shot = Mock()

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._on_support_prepare_worker_finished(page, old_worker)

        single_shot.assert_not_called()
        page._request_support_prepare.assert_not_called()
        self.assertTrue(page._support_prepare_pending)

    def test_support_prepare_request_waits_while_restart_is_scheduled(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._support_prepare_start_scheduled = True
        page._support_prepare_pending = False
        page._support_prepare_runtime = SimpleNamespace(is_running=Mock(return_value=False), start_qthread_worker=Mock())

        logs_page.LogsPage._request_support_prepare(page)

        page._support_prepare_runtime.start_qthread_worker.assert_not_called()
        self.assertTrue(page._support_prepare_pending)

    def test_support_prepare_result_ignored_when_new_prepare_is_pending(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._support_prepare_pending = True
        page._support_prepare_runtime = Mock()
        page._support_prepare_runtime.is_current.return_value = True
        page._logs = Mock()
        page._render_send_status_label = Mock()
        page.window = Mock(return_value=None)

        with patch.object(logs_page, "apply_support_feedback") as apply_feedback:
            logs_page.LogsPage._on_support_prepare_finished(page, 9, object())

        apply_feedback.assert_not_called()
        page._render_send_status_label.assert_not_called()

    def test_support_prepare_error_ignored_when_new_prepare_is_pending(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._support_prepare_pending = True
        page._support_prepare_runtime = Mock()
        page._support_prepare_runtime.is_current.return_value = True
        page._logs = Mock()
        page._logs.build_support_error_feedback.return_value = SimpleNamespace(
            status_text="old",
            status_tone="error",
            infobar_title="Ошибка",
            infobar_content="old",
        )
        page._render_send_status_label = Mock()
        page.window = Mock(return_value=None)

        with (
            patch.object(logs_page, "InfoBar") as info_bar,
            patch.object(logs_page, "log") as log_mock,
        ):
            logs_page.LogsPage._on_support_prepare_failed(page, 9, "old error")

        page._logs.build_support_error_feedback.assert_not_called()
        page._render_send_status_label.assert_not_called()
        info_bar.warning.assert_not_called()
        log_mock.assert_not_called()

    def test_open_folder_error_ignored_when_new_open_is_pending(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._open_folder_pending = True
        page._open_folder_runtime = Mock()
        page._open_folder_runtime.is_current.return_value = True

        with patch.object(logs_page, "log") as log_mock:
            logs_page.LogsPage._on_open_logs_folder_failed(page, 10, "old error")

        log_mock.assert_not_called()

    def test_cleanup_does_not_block_one_shot_support_or_open_folder_workers(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._logs_overview_restart_scheduled = True
        page._spin_timer = Mock()
        page._stop_logs_overview_worker = Mock()
        page._stop_support_prepare_worker = Mock()
        page._open_folder_runtime = Mock()
        page._open_folder_pending = True
        page._open_folder_start_scheduled = True
        page._pending_log_text_append = "old log"
        page._log_text_append_scheduled = True
        page._stop_log_source = Mock()

        logs_page.LogsPage.cleanup(page)

        self.assertTrue(page._cleanup_in_progress)
        self.assertFalse(page._logs_overview_restart_scheduled)
        self.assertFalse(page._open_folder_pending)
        self.assertFalse(page._open_folder_start_scheduled)
        self.assertEqual(page._pending_log_text_append, "")
        self.assertFalse(page._log_text_append_scheduled)
        page._spin_timer.stop.assert_called_once()
        page._stop_logs_overview_worker.assert_called_once_with(blocking=False)
        page._stop_support_prepare_worker.assert_called_once_with(blocking=False)
        page._open_folder_runtime.stop.assert_called_once_with(
            blocking=False,
            log_fn=logs_page.log,
            warning_prefix="Logs open folder worker",
        )
        page._open_folder_runtime.cancel.assert_called_once()
        page._stop_log_source.assert_called_once_with(blocking=False)

    def test_copy_log_uses_cached_text_without_reading_log_widget(self) -> None:
        class LogText:
            def toPlainText(self):  # noqa: N802
                raise AssertionError("GUI log text should not be read during copy")

        clipboard = SimpleNamespace(setText=Mock())
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page.log_text = LogText()
        page._log_text_cache = "cached log"
        page._ui_language = "ru"
        page._set_info_text = Mock()

        with patch.object(logs_page, "QApplication", SimpleNamespace(clipboard=Mock(return_value=clipboard))):
            logs_page.LogsPage._copy_log(page)

        clipboard.setText.assert_called_once_with("cached log")
        page._set_info_text.assert_called_once()

    def test_log_text_appends_are_coalesced_for_next_gui_turn(self) -> None:
        class ScrollBar:
            def value(self):
                return 10

            def maximum(self):
                return 10

            def setValue(self, value):  # noqa: N802
                self.last_value = value

        class Cursor:
            MoveOperation = SimpleNamespace(End="end")

            def __init__(self):
                self.inserted = []

            def movePosition(self, _operation):  # noqa: N802
                return True

            def insertText(self, text):  # noqa: N802
                self.inserted.append(text)

        class LogText:
            def __init__(self):
                self.cursor = Cursor()
                self.scrollbar = ScrollBar()

            def verticalScrollBar(self):  # noqa: N802
                return self.scrollbar

            def setUpdatesEnabled(self, _enabled):  # noqa: N802
                return None

            def textCursor(self):  # noqa: N802
                return self.cursor

            def setTextCursor(self, _cursor):  # noqa: N802
                return None

        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._pending_log_text_append = ""
        page._log_text_append_scheduled = False
        page._log_text_cache = ""
        page._error_pattern = Mock(search=Mock(return_value=False))
        page._exclude_pattern = Mock(search=Mock(return_value=False))
        page._add_errors = Mock()
        page.log_text = LogText()
        scheduled_callbacks = []
        single_shot = Mock(side_effect=lambda _delay, callback: scheduled_callbacks.append(callback))

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._append_text(page, "first\n")
            logs_page.LogsPage._append_text(page, "second\n")

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        self.assertEqual(page._log_text_cache, "first\nsecond")
        self.assertEqual(page.log_text.cursor.inserted, [])

        scheduled_callbacks[0]()

        self.assertEqual(page.log_text.cursor.inserted, ["first\nsecond\n"])
        self.assertFalse(page._log_text_append_scheduled)
        self.assertEqual(page._pending_log_text_append, "")

    def _page_with_fake_log_text(self):
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._cleanup_in_progress = False
        page._pending_log_text_append = ""
        page._log_text_append_scheduled = False
        page._log_text_cache = ""
        page._first_log_content_timing_done = True
        page._error_pattern = Mock(search=Mock(side_effect=lambda line: "ERROR" in line))
        page._exclude_pattern = Mock(search=Mock(return_value=False))
        page._add_errors = Mock()
        page.log_text = _FakeLogText()
        return page

    def test_big_log_snapshot_is_inserted_in_chunks_between_gui_turns(self) -> None:
        chunk_lines = logs_page.LOG_APPEND_CHUNK_LINES
        snapshot = "".join(f"line {index}\n" for index in range(chunk_lines * 2 + 7))
        page = self._page_with_fake_log_text()
        scheduled_callbacks = []
        single_shot = Mock(side_effect=lambda _delay, callback: scheduled_callbacks.append(callback))

        with patch.object(logs_page, "QTimer", SimpleNamespace(singleShot=single_shot)):
            logs_page.LogsPage._append_text(page, snapshot)
            self.assertEqual(len(scheduled_callbacks), 1)

            scheduled_callbacks.pop(0)()

            # Первая порция уже на экране, остаток ждёт следующего прохода Qt.
            self.assertEqual(len(page.log_text.inserted), 1)
            self.assertEqual(len(page.log_text.inserted[0].splitlines()), chunk_lines)
            self.assertTrue(page._log_text_append_scheduled)
            self.assertEqual(len(scheduled_callbacks), 1)

            # Строка, пришедшая во время вставки, встаёт в конец очереди.
            logs_page.LogsPage._append_text(page, "live tail\n")
            self.assertEqual(len(scheduled_callbacks), 1)

            while scheduled_callbacks:
                scheduled_callbacks.pop(0)()

        self.assertEqual([len(part.splitlines()) for part in page.log_text.inserted], [chunk_lines, chunk_lines, 8])
        self.assertEqual("".join(page.log_text.inserted), snapshot + "live tail\n")
        self.assertFalse(page._log_text_append_scheduled)
        self.assertEqual(page._pending_log_text_append, "")
        self.assertTrue(all(call.args[0] == 0 for call in single_shot.call_args_list))

    def test_error_lines_of_one_chunk_reach_errors_panel_as_one_batch(self) -> None:
        page = self._page_with_fake_log_text()

        logs_page.LogsPage._append_text_now(page, "ok\n[ERROR] first\n\nok again\n[ERROR] second  \n[ERROR] third\n")

        page._add_errors.assert_called_once_with(["[ERROR] first", "[ERROR] second", "[ERROR] third"])

    def test_chunk_without_errors_does_not_touch_errors_panel(self) -> None:
        page = self._page_with_fake_log_text()

        logs_page.LogsPage._append_text_now(page, "ok\nfine\n")

        page._add_errors.assert_not_called()

    def test_errors_batch_recalculates_panel_once(self) -> None:
        page = logs_page.LogsPage.__new__(logs_page.LogsPage)
        page._ui_language = "ru"
        page._errors_count = 2
        page._ensure_logs_secondary_panels = Mock(return_value=True)
        page._update_errors_text_height = Mock()
        scrollbar = Mock(maximum=Mock(return_value=40))
        page.errors_text = Mock(verticalScrollBar=Mock(return_value=scrollbar))
        page.errors_count_label = Mock()

        logs_page.LogsPage._add_errors(page, ["[ERROR] first", "[ERROR] second", "[ERROR] third"])

        self.assertEqual(page._errors_count, 5)
        self.assertEqual(
            [call.args[0] for call in page.errors_text.append.call_args_list],
            ["[ERROR] first", "[ERROR] second", "[ERROR] third"],
        )
        page.errors_count_label.setText.assert_called_once()
        self.assertIn("5", page.errors_count_label.setText.call_args.args[0])
        page._update_errors_text_height.assert_called_once_with()
        scrollbar.setValue.assert_called_once_with(40)

    def test_clearing_log_view_drops_chunks_not_inserted_yet(self) -> None:
        page = self._page_with_fake_log_text()
        page.log_text.clear = Mock()
        page._pending_log_text_append = "old file tail\n"
        page._log_text_append_scheduled = True

        logs_page.LogsPage._clear_log_view_silent(page)
        logs_page.LogsPage._flush_pending_log_text_append(page)

        page.log_text.clear.assert_called_once_with()
        self.assertEqual(page.log_text.inserted, [])
        self.assertFalse(page._log_text_append_scheduled)


if __name__ == "__main__":
    unittest.main()
