import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from blockcheck.ui.strategy_scan_page import StrategyScanPage


class StrategyScanApplyRuntimeArchitectureTests(unittest.TestCase):
    def test_strategy_apply_state_uses_shared_latest_value_helper(self) -> None:
        import blockcheck.ui.strategy_scan_page as strategy_scan_page
        from ui.latest_value_worker_state import LatestValueWorkerState

        init_source = inspect.getsource(StrategyScanPage.__init__)
        request_source = inspect.getsource(StrategyScanPage._request_strategy_apply)
        finished_source = inspect.getsource(StrategyScanPage._on_strategy_apply_runtime_finished)
        cleanup_source = inspect.getsource(StrategyScanPage.cleanup)

        self.assertTrue(hasattr(strategy_scan_page, "LatestValueWorkerState"))
        self.assertIs(strategy_scan_page.LatestValueWorkerState, LatestValueWorkerState)
        self.assertIn("_strategy_apply_state = LatestValueWorkerState", init_source)
        self.assertIn("_strategy_apply_state_obj()", request_source)
        self.assertIn("schedule_pending_after_finish", finished_source)
        self.assertIn("_strategy_apply_state_obj().reset()", cleanup_source)

    def test_strategy_apply_uses_runtime_not_manual_page_worker(self) -> None:
        page_source = inspect.getsource(StrategyScanPage)
        request_source = "\n".join(
            (
                inspect.getsource(StrategyScanPage._request_strategy_apply),
                inspect.getsource(StrategyScanPage._start_strategy_apply_worker),
            )
        )
        finished_source = inspect.getsource(StrategyScanPage._on_strategy_apply_finished)
        failed_source = inspect.getsource(StrategyScanPage._on_strategy_apply_failed)
        cleanup_source = inspect.getsource(StrategyScanPage.cleanup)

        self.assertIn("_strategy_apply_runtime = OneShotWorkerRuntime()", page_source)
        self.assertIn("_strategy_apply_state = LatestValueWorkerState", page_source)
        self.assertIn("state.is_busy()", request_source)
        self.assertIn("start_qthread_worker", request_source)
        self.assertIn("bind_worker", request_source)
        self.assertIn("_strategy_apply_runtime.is_current", finished_source)
        self.assertIn("_strategy_apply_runtime.is_current", failed_source)
        self.assertIn("_strategy_apply_runtime.stop", cleanup_source)
        self.assertIn("_strategy_apply_runtime.cancel", cleanup_source)
        self.assertNotIn("_strategy_apply_worker =", page_source)
        self.assertNotIn("_strategy_apply_request_id", page_source)
        self.assertNotIn("worker.start()", request_source)

    def test_strategy_apply_queues_latest_request_while_worker_runs(self) -> None:
        class _Runtime:
            def is_running(self) -> bool:
                return True

        page = StrategyScanPage.__new__(StrategyScanPage)
        page._strategy_apply_runtime = _Runtime()
        page._strategy_apply_pending = None
        page._strategy_apply_start_scheduled = False
        page.create_strategy_apply_worker = Mock()

        old = SimpleNamespace(strategy_args="--lua-desync=fake", strategy_name="old", apply_lines=("--a",))
        new = SimpleNamespace(strategy_args="--lua-desync=multisplit", strategy_name="new", apply_lines=("--b",))
        StrategyScanPage._request_strategy_apply(page, old)
        StrategyScanPage._request_strategy_apply(page, new)

        page.create_strategy_apply_worker.assert_not_called()
        self.assertEqual(
            page._strategy_apply_pending,
            {
                "strategy_args": "--lua-desync=multisplit",
                "strategy_name": "new",
                "apply_lines": ("--b",),
            },
        )

    def test_strategy_apply_pending_restarts_after_event_loop_turn(self) -> None:
        import blockcheck.ui.strategy_scan_page as strategy_scan_page

        pending = {
            "strategy_args": "--dpi-desync=split",
            "strategy_name": "new",
        }
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._cleanup_in_progress = False
        page._strategy_apply_pending = pending
        page._strategy_apply_start_scheduled = False
        page._start_strategy_apply_worker = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(strategy_scan_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            StrategyScanPage._on_strategy_apply_runtime_finished(page, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page._start_strategy_apply_worker.assert_not_called()

        single_shot.call_args.args[1]()

        page._start_strategy_apply_worker.assert_called_once_with(pending)
        self.assertIsNone(page._strategy_apply_pending)

    def test_stale_strategy_apply_runtime_finish_does_not_restart_pending_apply(self) -> None:
        pending = {
            "strategy_args": "--dpi-desync=split",
            "strategy_name": "new",
        }
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._strategy_apply_runtime = SimpleNamespace(request_id=2)
        page._strategy_apply_pending = pending
        page._schedule_strategy_apply_worker_start = Mock()

        StrategyScanPage._on_strategy_apply_runtime_finished(page, SimpleNamespace(_request_id=1))

        page._schedule_strategy_apply_worker_start.assert_not_called()
        self.assertEqual(page._strategy_apply_pending, pending)

    def test_strategy_apply_result_ignored_when_new_apply_is_pending(self) -> None:
        import blockcheck.ui.strategy_scan_page as strategy_scan_page

        page = StrategyScanPage.__new__(StrategyScanPage)
        page._cleanup_in_progress = False
        page._strategy_apply_pending = {
            "strategy_args": "--dpi-desync=split",
            "strategy_name": "new",
        }
        page._strategy_apply_runtime = Mock()
        page._strategy_apply_runtime.is_current.return_value = True
        page._blockcheck = Mock()
        page._blockcheck.build_apply_success_plan.return_value = SimpleNamespace(
            title_key="title",
            title_default="Title",
            body_text="Body",
        )
        page.window = Mock(return_value=object())

        with patch.object(strategy_scan_page.InfoBarHelper, "success") as success:
            StrategyScanPage._on_strategy_apply_finished(page, 5, object())

        page._blockcheck.build_apply_success_plan.assert_not_called()
        success.assert_not_called()

    def test_strategy_apply_failure_ignored_when_new_apply_is_pending(self) -> None:
        import blockcheck.ui.strategy_scan_page as strategy_scan_page

        page = StrategyScanPage.__new__(StrategyScanPage)
        page._cleanup_in_progress = False
        page._strategy_apply_pending = {
            "strategy_args": "--dpi-desync=split",
            "strategy_name": "new",
        }
        page._strategy_apply_runtime = Mock()
        page._strategy_apply_runtime.is_current.return_value = True
        page._blockcheck = Mock()
        page._blockcheck.build_apply_error_plan.return_value = SimpleNamespace(
            title_key="title",
            title_default="Title",
            body_text="Body",
        )
        page.window = Mock(return_value=object())

        with (
            patch.object(strategy_scan_page.logger, "warning") as warning_log,
            patch.object(strategy_scan_page.InfoBarHelper, "warning") as warning_bar,
        ):
            StrategyScanPage._on_strategy_apply_failed(page, 5, "stale error")

        warning_log.assert_not_called()
        page._blockcheck.build_apply_error_plan.assert_not_called()
        warning_bar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
