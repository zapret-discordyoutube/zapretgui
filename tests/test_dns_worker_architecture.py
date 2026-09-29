from __future__ import annotations

import importlib.util
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from app.feature_facades.dns import build_dns_feature
from dns import commands as dns_commands
from dns import dns_check_plans, dns_check_worker, page_workers
from dns.ui.dns_check_page import DNSCheckPage


class DnsWorkerArchitectureTests(unittest.TestCase):
    def test_network_action_workers_receive_feature_action_callables(self) -> None:
        feature_source = inspect.getsource(build_dns_feature)
        worker_source = "\n".join(
            (
                inspect.getsource(page_workers.DnsFlushCacheWorker),
                inspect.getsource(page_workers.DnsIspWarningWorker),
                inspect.getsource(page_workers.DnsApplyWorker),
                inspect.getsource(page_workers.DnsLatencyWorker),
            )
        )

        self.assertNotIn("dns_feature=feature", feature_source)
        self.assertNotIn("self._dns =", worker_source)
        self.assertNotIn("self._dns.", worker_source)
        self.assertNotIn("import dns.public", worker_source)
        self.assertNotIn("dns_public.", worker_source)
        self.assertIn("from dns import page_plans", worker_source)

        for expected in (
            "flush_dns_cache=flush_dns_cache",
            "apply_dns=apply_dns",
            "reset_to_auto=reset_to_auto",
            "load_state=load_state",
            "measure_dns_latency=measure_dns_latency",
            "is_isp_dns_warning_shown=is_isp_dns_warning_shown",
            "mark_isp_dns_warning_shown=mark_isp_dns_warning_shown",
        ):
            self.assertIn(expected, feature_source)

        for expected in (
            "_flush_dns_cache",
            "_apply_dns",
            "_reset_to_auto",
            "_load_state",
            "_measure_dns_latency",
            "_is_isp_dns_warning_shown",
            "_mark_isp_dns_warning_shown",
        ):
            self.assertIn(expected, worker_source)

        for removed in ("create_force_dns_action_worker", "create_connectivity_test_worker", "apply_custom_dns"):
            self.assertNotIn(removed, feature_source)

    def test_dns_check_workers_receive_feature_action_callables(self) -> None:
        feature_source = inspect.getsource(build_dns_feature)
        worker_source = "\n".join(
            (
                inspect.getsource(dns_check_worker.DNSCheckWorker),
                inspect.getsource(dns_check_worker.DNSCheckSaveWorker),
            )
        )

        for expected in (
            "run_dns_poisoning_check=run_dns_poisoning_check",
            "save_dns_check_results=save_dns_check_results",
        ):
            self.assertIn(expected, feature_source)

        for expected in (
            "_run_dns_poisoning_check",
            "_save_dns_check_results",
        ):
            self.assertIn(expected, worker_source)

        self.assertNotIn("from dns import commands", worker_source)
        self.assertNotIn("from dns.commands import", worker_source)
        self.assertNotIn("dns_commands.", worker_source)

    def test_dns_check_save_action_lives_in_commands_not_plans(self) -> None:
        plans_source = inspect.getsource(dns_check_plans)
        commands_source = inspect.getsource(dns_commands.save_dns_check_results)

        self.assertNotIn("def save_results_text", plans_source)
        self.assertNotIn("open(", plans_source)
        self.assertNotIn("os.startfile", plans_source)
        self.assertIn("open(", commands_source)
        self.assertIn("os.startfile", commands_source)

    def test_dns_check_page_uses_one_shot_runtime_for_check_and_save(self) -> None:
        page_source = inspect.getsource(DNSCheckPage)
        start_source = inspect.getsource(DNSCheckPage.start_check)
        save_source = inspect.getsource(DNSCheckPage._start_save_results_worker)
        cleanup_source = inspect.getsource(DNSCheckPage.cleanup)

        self.assertIn("OneShotWorkerRuntime", page_source)
        for name in (
            "_check_runtime",
            "_save_runtime",
        ):
            self.assertIn(name, page_source)
            self.assertIn(f"{name}.stop", cleanup_source)
        self.assertIn("start_qobject_worker", start_source)
        self.assertIn("start_qthread_worker", save_source)
        for source in (start_source, save_source):
            self.assertNotIn("worker.start()", source)
        self.assertNotIn("self.thread = QThread", start_source)

    def test_dns_full_check_uses_shared_latest_worker_state(self) -> None:
        from ui.latest_value_worker_state import LatestValueWorkerState

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._check_runtime = SimpleNamespace(is_running=Mock(return_value=False))

        init_source = inspect.getsource(DNSCheckPage.__init__)
        start_source = inspect.getsource(DNSCheckPage.start_check)
        schedule_source = inspect.getsource(DNSCheckPage._schedule_full_dns_check_start)
        cleanup_source = inspect.getsource(DNSCheckPage.cleanup)

        self.assertIsInstance(DNSCheckPage._check_state_obj(page), LatestValueWorkerState)
        self.assertNotIn("self._check_pending = False", init_source)
        self.assertNotIn("self._check_start_scheduled = False", init_source)
        self.assertIn("_check_state_obj()", start_source)
        self.assertIn("_check_state_obj()", schedule_source)
        self.assertIn("_check_state_obj().reset()", cleanup_source)

    def test_dns_check_save_uses_shared_latest_worker_state(self) -> None:
        from ui.latest_value_worker_state import LatestValueWorkerState

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._save_runtime = SimpleNamespace(is_running=Mock(return_value=False))

        init_source = inspect.getsource(DNSCheckPage.__init__)
        start_source = inspect.getsource(DNSCheckPage._start_save_results_worker)
        schedule_source = inspect.getsource(DNSCheckPage._schedule_save_results_worker_start)
        cleanup_source = inspect.getsource(DNSCheckPage.cleanup)

        self.assertIsInstance(DNSCheckPage._save_results_state_obj(page), LatestValueWorkerState)
        self.assertNotIn("self._save_results_pending: dict[str, str] | None = None", init_source)
        self.assertNotIn("self._save_results_start_scheduled = False", init_source)
        self.assertIn("_save_results_state_obj()", start_source)
        self.assertIn("_save_results_state_obj()", schedule_source)
        self.assertIn("_save_results_state_obj().reset()", cleanup_source)

    def test_dns_check_save_queues_while_worker_runs(self) -> None:
        page = DNSCheckPage.__new__(DNSCheckPage)
        page._save_runtime = SimpleNamespace(is_running=Mock(return_value=True), start_qthread_worker=Mock())
        page._save_results_pending = None

        DNSCheckPage._start_save_results_worker(page, file_path="first.txt", plain_text="latest")

        page._save_runtime.start_qthread_worker.assert_not_called()
        self.assertEqual(page._save_results_pending, {"file_path": "first.txt", "plain_text": "latest"})

    def test_dns_check_save_while_worker_runs_defers_result_text_read(self) -> None:
        page = DNSCheckPage.__new__(DNSCheckPage)
        page._save_runtime = SimpleNamespace(is_running=Mock(return_value=True), start_qthread_worker=Mock())
        page._save_results_pending = None
        page._save_results_start_scheduled = False
        page._results_plain_text_cache = "latest dns report"

        DNSCheckPage._start_save_results_worker(page, file_path="first.txt", plain_text=None)

        page._save_runtime.start_qthread_worker.assert_not_called()
        self.assertEqual(page._save_results_pending, {"file_path": "first.txt", "plain_text": None})

    def test_dns_check_save_uses_cached_result_text(self) -> None:
        page = DNSCheckPage.__new__(DNSCheckPage)
        page._save_runtime = SimpleNamespace(is_running=Mock(return_value=False), start_qthread_worker=Mock())
        page._save_results_pending = None
        page._save_results_start_scheduled = False
        page._results_plain_text_cache = "cached dns report"
        page.create_dns_check_save_worker = Mock(return_value="worker")

        DNSCheckPage._start_save_results_worker(page, file_path="first.txt", plain_text=None)

        page._save_runtime.start_qthread_worker.assert_called_once()
        worker_factory = page._save_runtime.start_qthread_worker.call_args.kwargs["worker_factory"]

        self.assertEqual(worker_factory(7), "worker")
        page.create_dns_check_save_worker.assert_called_once_with(
            7,
            file_path="first.txt",
            plain_text="cached dns report",
        )

    def test_dns_check_pending_save_hides_previous_save_result(self) -> None:
        import dns.ui.dns_check_page as dns_check_page

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._save_runtime = SimpleNamespace(is_current=Mock(return_value=True))
        page._save_results_pending = {"file_path": "second.txt", "plain_text": "newer"}
        page.window = Mock(return_value=object())
        plan = SimpleNamespace(success=True, title="saved", content="old result")
        success = Mock()
        error = Mock()

        with patch.object(dns_check_page, "InfoBar", SimpleNamespace(success=success, error=error)):
            DNSCheckPage._on_save_results_finished(page, 1, plan)

        success.assert_not_called()
        error.assert_not_called()

    def test_dns_check_pending_save_restarts_after_event_loop_turn(self) -> None:
        import dns.ui.dns_check_page as dns_check_page

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._save_results_pending = {"file_path": "first.txt", "plain_text": "latest"}
        page._start_save_results_worker = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(dns_check_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            DNSCheckPage._on_save_results_worker_finished(page, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page._start_save_results_worker.assert_not_called()

        single_shot.call_args.args[1]()

        page._start_save_results_worker.assert_called_once_with(
            file_path="first.txt",
            plain_text="latest",
        )

    def test_stale_dns_check_save_finish_does_not_restart_pending_save(self) -> None:
        import dns.ui.dns_check_page as dns_check_page

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._save_runtime = SimpleNamespace(worker=object())
        page._save_results_pending = {"file_path": "first.txt", "plain_text": "latest"}
        page._save_results_start_scheduled = False
        page._start_save_results_worker = Mock()
        single_shot = Mock()

        with patch.object(dns_check_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            DNSCheckPage._on_save_results_worker_finished(page, object())

        single_shot.assert_not_called()
        page._start_save_results_worker.assert_not_called()
        self.assertEqual(page._save_results_pending, {"file_path": "first.txt", "plain_text": "latest"})

    def test_dns_check_scheduled_save_uses_latest_pending_payload(self) -> None:
        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._save_results_pending = {"file_path": "second.txt", "plain_text": "newer"}
        page._save_results_start_scheduled = True
        page._start_save_results_worker = Mock()

        DNSCheckPage._run_scheduled_save_results_worker_start(page)

        page._start_save_results_worker.assert_called_once_with(
            file_path="second.txt",
            plain_text="newer",
        )

    def test_stale_dns_full_check_finish_does_not_restart_pending_check(self) -> None:
        import dns.ui.dns_check_page as dns_check_page

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._check_runtime = SimpleNamespace(request_id=2)
        page._check_pending = True
        page._check_start_scheduled = False
        page.start_check = Mock()
        single_shot = Mock()

        with patch.object(dns_check_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            DNSCheckPage._on_check_worker_finished(page, 1, object())

        single_shot.assert_not_called()
        page.start_check.assert_not_called()
        self.assertTrue(page._check_pending)

    def test_dns_check_cleanup_does_not_block_gui_for_one_shot_workers(self) -> None:
        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._check_runtime = Mock()
        page._save_runtime = Mock()
        page._check_pending = True
        page._check_start_scheduled = True
        page._save_results_pending = {"file_path": "dns.txt", "plain_text": "old"}
        page._save_results_start_scheduled = True

        DNSCheckPage.cleanup(page)

        self.assertTrue(page._cleanup_in_progress)
        self.assertFalse(page._check_pending)
        self.assertFalse(page._check_start_scheduled)
        self.assertIsNone(page._save_results_pending)
        self.assertFalse(page._save_results_start_scheduled)
        page._check_runtime.stop.assert_called_once_with(
            blocking=False,
            log_fn=ANY,
            warning_prefix="DNS check worker",
        )
        page._check_runtime.cancel.assert_called_once()
        page._save_runtime.stop.assert_called_once_with(
            blocking=False,
            log_fn=ANY,
            warning_prefix="DNS check save worker",
        )
        page._save_runtime.cancel.assert_called_once()

    def test_dns_full_check_queues_while_worker_runs(self) -> None:
        page = DNSCheckPage.__new__(DNSCheckPage)
        page._check_runtime = SimpleNamespace(is_running=Mock(return_value=True))
        page._check_pending = False
        page._set_log_available = Mock()
        page._apply_interaction_state = Mock()
        page._set_status = Mock()

        DNSCheckPage.start_check(page)

        self.assertTrue(page._check_pending)
        page._set_log_available.assert_not_called()
        page._apply_interaction_state.assert_not_called()
        page._set_status.assert_not_called()

    def test_dns_pending_full_check_restarts_after_event_loop_turn(self) -> None:
        import dns.ui.dns_check_page as dns_check_page

        page = DNSCheckPage.__new__(DNSCheckPage)
        page._cleanup_in_progress = False
        page._check_pending = True
        page._check_start_scheduled = False
        page.start_check = Mock()
        single_shot = Mock(side_effect=lambda _delay, _callback: None)

        with patch.object(dns_check_page, "QTimer", SimpleNamespace(singleShot=single_shot), create=True):
            DNSCheckPage._on_check_worker_finished(page, 1, object())

        single_shot.assert_called_once()
        self.assertEqual(single_shot.call_args.args[0], 0)
        page.start_check.assert_not_called()

        single_shot.call_args.args[1]()

        self.assertFalse(page._check_pending)
        page.start_check.assert_called_once_with()

    def test_dns_layer_has_no_force_or_startup_dns(self) -> None:
        for module in ("dns.dns_core", "dns.dns_force", "dns.dns_worker", "main.post_startup_dns"):
            with self.subTest(module=module):
                self.assertIsNone(importlib.util.find_spec(module))

    def test_dns_feature_does_not_expose_heavy_direct_commands(self) -> None:
        feature = build_dns_feature()

        for attr_name in (
            "load_page_data",
            "load_state",
            "apply_dns",
            "reset_to_auto",
            "refresh_dns_info",
            "apply_auto_dns",
            "apply_provider_dns",
            "apply_custom_dns",
            "get_force_dns_status",
            "is_isp_dns_warning_shown",
            "mark_isp_dns_warning_shown",
            "enable_force_dns",
            "disable_force_dns",
            "flush_dns_cache",
            "run_connectivity_test",
            "run_dns_poisoning_check",
            "save_dns_check_results",
            "run_quick_dns_check",
        ):
            self.assertFalse(hasattr(feature, attr_name), attr_name)


if __name__ == "__main__":
    unittest.main()
