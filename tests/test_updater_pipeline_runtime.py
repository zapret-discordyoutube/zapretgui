from __future__ import annotations

import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from updater.update_page_runtime import UpdateDownloadState, UpdateFoundState, UpdatePageRuntime


class UpdaterPipelineRuntimeTests(unittest.TestCase):
    def _runtime(self) -> UpdatePageRuntime:
        runtime = UpdatePageRuntime.__new__(UpdatePageRuntime)
        runtime._cleanup_in_progress = False
        runtime._download_state = UpdateDownloadState(is_installing=True)
        runtime._view = Mock()
        runtime._runtime_actions = SimpleNamespace(
            request_exit=Mock(),
            mark_stopped=Mock(),
        )
        runtime._restart_dpi_after_update = Mock()
        return runtime

    def test_preflight_connectivity_failure_requests_managed_dpi_stop(self) -> None:
        runtime = self._runtime()
        runtime._request_update_dpi_stop = Mock()
        artifact = object()

        UpdatePageRuntime._on_update_preflight_ready(
            runtime,
            SimpleNamespace(artifact=artifact, connectivity_ok=False),
        )

        self.assertIs(runtime._download_state.artifact, artifact)
        runtime._request_update_dpi_stop.assert_called_once_with(after_stop="download")

    def test_verified_handoff_stops_dpi_before_installer(self) -> None:
        runtime = self._runtime()
        runtime._request_update_dpi_stop = Mock()
        handoff = object()

        UpdatePageRuntime._on_update_handoff_ready(runtime, handoff)

        self.assertIs(runtime._download_state.handoff, handoff)
        runtime._view.mark_update_download_complete.assert_called_once_with()
        runtime._request_update_dpi_stop.assert_called_once_with(after_stop="installer")

    def test_dpi_stop_result_updates_runtime_state_on_main_receiver(self) -> None:
        runtime = self._runtime()
        runtime._update_dpi_stop_runtime = Mock()
        runtime._update_dpi_stop_runtime.is_current.return_value = True
        runtime._continue_after_update_dpi_stop = Mock()

        UpdatePageRuntime._on_update_dpi_stop_finished(
            runtime,
            7,
            True,
            True,
            "",
        )

        runtime._runtime_actions.mark_stopped.assert_called_once_with()
        self.assertTrue(runtime._download_state.dpi_stopped_by_update)
        runtime._continue_after_update_dpi_stop.assert_called_once_with()

    def test_handoff_starts_installer_immediately_when_dpi_already_stopped(self) -> None:
        runtime = self._runtime()
        runtime._download_state.dpi_stopped_by_update = True
        runtime._start_update_installer_stage = Mock()

        UpdatePageRuntime._on_update_handoff_ready(runtime, object())

        runtime._start_update_installer_stage.assert_called_once_with()

    def test_installer_launch_schedules_normal_application_exit(self) -> None:
        runtime = self._runtime()
        callbacks = []

        with patch(
            "updater.update_page_runtime.QTimer.singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            UpdatePageRuntime._on_update_installer_launched(runtime)

        self.assertTrue(runtime._download_state.installer_launched)
        self.assertEqual(len(callbacks), 1)
        runtime._runtime_actions.request_exit.assert_not_called()
        callbacks[0]()
        runtime._runtime_actions.request_exit.assert_called_once_with(stop_dpi=False)

    def test_failure_restarts_only_dpi_stopped_by_update(self) -> None:
        runtime = self._runtime()
        runtime._download_state.dpi_stopped_by_update = True

        UpdatePageRuntime._fail_update_pipeline(runtime, "bad sha")

        runtime._view.mark_update_download_failed.assert_called_once_with("bad sha")
        runtime._restart_dpi_after_update.assert_called_once_with(
            context="неудачного обновления"
        )
        self.assertFalse(runtime._download_state.is_installing)

    def test_page_runtime_is_qobject_receiver_for_queued_worker_signals(self) -> None:
        source = inspect.getsource(UpdatePageRuntime)
        bind_source = inspect.getsource(UpdatePageRuntime._bind_update_download_signals)
        self.assertIn("class UpdatePageRuntime(QObject)", source)
        self.assertIn("worker.progress_bytes.connect(self._on_update_download_progress)", bind_source)
        self.assertNotIn("lambda p, d, t", bind_source)



class UpdateCheckErrorPresentationTests(unittest.TestCase):
    """Сбой проверки показывается как ошибка, а не как «Обновлений нет»."""

    def _runtime(self) -> UpdatePageRuntime:
        runtime = UpdatePageRuntime.__new__(UpdatePageRuntime)
        runtime._cleanup_in_progress = False
        runtime._found_state = UpdateFoundState()
        runtime._view = Mock()
        runtime._manual_check_token = 5
        runtime._update_check_unsubscribe = object()
        runtime._updater_feature = SimpleNamespace(finish_update_check=Mock(return_value=True))
        runtime._is_dev_update_channel = lambda: True
        runtime._app_version = lambda: "21.1.5.79"
        return runtime

    def test_lookup_error_is_published_as_check_error(self) -> None:
        runtime = self._runtime()

        UpdatePageRuntime._on_version_found(runtime, "dev", {"error": "Forgejo: нет ответа"})
        UpdatePageRuntime._finish_checking_workflow(runtime)

        result = runtime._updater_feature.finish_update_check.call_args.args[0]
        self.assertEqual(result["error"], "Forgejo: нет ответа")
        self.assertFalse(result["has_update"])

    def test_error_is_shown_directly_when_nobody_listens(self) -> None:
        runtime = self._runtime()
        runtime._update_check_unsubscribe = None

        UpdatePageRuntime._on_version_found(runtime, "dev", {"error": "Зеркала: нет ответа"})
        UpdatePageRuntime._finish_checking_workflow(runtime)

        runtime._view.show_update_check_error.assert_called_once_with("Зеркала: нет ответа")
        runtime._view.finish_checking.assert_not_called()

    def test_successful_answer_clears_previous_error(self) -> None:
        runtime = self._runtime()

        UpdatePageRuntime._on_version_found(runtime, "dev", {"error": "временный сбой"})
        UpdatePageRuntime._on_version_found(runtime, "dev", {"version": "21.1.5.80", "source": "Forgejo"})
        UpdatePageRuntime._finish_checking_workflow(runtime)

        result = runtime._updater_feature.finish_update_check.call_args.args[0]
        self.assertIsNone(result["error"])
        self.assertTrue(result["has_update"])
        self.assertEqual(result["version"], "21.1.5.80")


if __name__ == "__main__":
    unittest.main()
