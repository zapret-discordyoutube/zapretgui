import inspect
import unittest
from unittest.mock import Mock

from blockcheck import strategy_scan_run_workflow
from blockcheck.ui.strategy_scan_page import StrategyScanPage


class StrategyScanRunRuntimeArchitectureTest(unittest.TestCase):
    def test_strategy_scan_run_worker_starts_through_shared_runtime(self) -> None:
        page_source = inspect.getsource(StrategyScanPage)
        start_source = inspect.getsource(StrategyScanPage._on_start)
        cleanup_source = inspect.getsource(StrategyScanPage.cleanup)
        workflow_source = inspect.getsource(strategy_scan_run_workflow.start_strategy_scan_worker)

        self.assertIn("_strategy_scan_run_runtime = OneShotWorkerRuntime()", page_source)
        self.assertIn("run_runtime=self._strategy_scan_run_runtime", start_source)
        self.assertIn("_strategy_scan_run_runtime.stop", cleanup_source)
        self.assertIn("run_runtime.start_qobject_worker", workflow_source)
        self.assertNotIn("self._worker", page_source)
        self.assertNotIn("worker.start()", workflow_source)

    def test_strategy_scan_page_leaves_run_worker_deletion_to_shared_runtime(self) -> None:
        finished_source = inspect.getsource(StrategyScanPage._on_finished)

        self.assertNotIn("delete_strategy_scan_worker_later", finished_source)
        self.assertNotIn("deleteLater", finished_source)

    def test_runtime_conflicting_stop_uses_strategy_scan_stop_path(self) -> None:
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._strategy_scan_run_runtime = Mock()
        page._strategy_scan_run_runtime.is_running.return_value = True
        page._on_stop = Mock()

        stopped = StrategyScanPage.request_runtime_conflicting_stop(page)

        self.assertTrue(stopped)
        page._on_stop.assert_called_once_with()

    def test_stop_while_queued_drops_the_scan_without_touching_zapret(self) -> None:
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._strategy_scan_run_runtime = Mock()
        page._strategy_scan_run_runtime.is_queued.return_value = True
        worker = Mock()
        page._scan_worker = worker
        page._on_finished = Mock()

        StrategyScanPage._on_stop(page)

        # Подбор не начинался: возвращать Zapret нечего, а из очереди он снят.
        worker.cancel_runtime_restore.assert_called_once_with()
        page._strategy_scan_run_runtime.stop.assert_called_once_with()
        (report,) = page._on_finished.call_args.args
        self.assertTrue(report.cancelled)
        # Отмена очереди читается как «Отменено», а не как «Ошибка подбора».
        from blockcheck.strategy_scan_page_plans import finalize_scan_report

        plan = finalize_scan_report(report, scan_protocol=report.scan_protocol, result_rows=[])
        self.assertTrue(plan.status_text.startswith("Отменено"), plan.status_text)

    def test_exit_during_scan_stops_it_and_reports_the_restore_when_it_finishes(self) -> None:
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._strategy_scan_run_runtime = Mock()
        page._strategy_scan_run_runtime.is_running.return_value = True
        page._on_stop = Mock()
        page._cleanup_in_progress = False
        page._restore_runtime_after_scan = Mock(return_value=True)
        page._request_strategy_scan_finalize = Mock()
        done = Mock()

        self.assertTrue(StrategyScanPage.request_exit_restore(page, done))
        page._on_stop.assert_called_once_with()
        done.assert_not_called()

        StrategyScanPage._on_finished(page, None)

        done.assert_called_once_with(True)
        # Второй конец подбора ничего не вызывает повторно.
        StrategyScanPage._on_finished(page, None)
        done.assert_called_once_with(True)

    def test_exit_without_a_running_scan_has_nothing_to_wait_for(self) -> None:
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._strategy_scan_run_runtime = Mock()
        page._strategy_scan_run_runtime.is_running.return_value = False
        page._on_stop = Mock()

        self.assertFalse(StrategyScanPage.request_exit_restore(page, Mock()))
        page._on_stop.assert_not_called()

    def test_window_port_waits_for_the_scan_page_and_calls_back_once(self) -> None:
        from unittest.mock import patch

        from PyQt6.QtWidgets import QApplication, QWidget

        from main.application_lifecycle_port import ApplicationLifecycleWindowPort

        app = QApplication.instance() or QApplication([])
        window = QWidget()
        self.addCleanup(window.deleteLater)
        callbacks: list = []
        scan_page = Mock()
        scan_page.request_exit_restore.side_effect = lambda on_done: callbacks.append(on_done) or True
        results: list[bool] = []

        with patch(
            "main.window_lifecycle_cleanup.iter_loaded_pages_for_close",
            return_value=iter([("other", object()), ("blockcheck", scan_page)]),
        ):
            waiting = ApplicationLifecycleWindowPort(window).finish_strategy_scan_before_exit(results.append)

        self.assertTrue(waiting)
        self.assertEqual(results, [])
        callbacks[0](True)
        callbacks[0](False)
        self.assertEqual(results, [True])
        del app

    def test_window_port_has_nothing_to_wait_for_without_a_scan(self) -> None:
        from unittest.mock import patch

        from PyQt6.QtWidgets import QApplication, QWidget

        from main.application_lifecycle_port import ApplicationLifecycleWindowPort

        app = QApplication.instance() or QApplication([])
        window = QWidget()
        self.addCleanup(window.deleteLater)
        idle_page = Mock()
        idle_page.request_exit_restore.return_value = False

        with patch(
            "main.window_lifecycle_cleanup.iter_loaded_pages_for_close",
            return_value=iter([("blockcheck", idle_page), ("about", object())]),
        ):
            waiting = ApplicationLifecycleWindowPort(window).finish_strategy_scan_before_exit(Mock())

        self.assertFalse(waiting)
        del app


if __name__ == "__main__":
    unittest.main()
