from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class StartupAutostartTests(unittest.TestCase):
    def test_preset_autostart_defers_startup_preset_snapshot_to_worker(self) -> None:
        from winws_runtime.runtime.autostart import start_dpi_autostart

        runtime_service = SimpleNamespace(
            snapshot=Mock(
                return_value=SimpleNamespace(
                    phase="autostart_pending",
                    running=False,
                    launch_method="zapret2_mode",
                )
            ),
            mark_start_failed=Mock(),
            mark_stopped=Mock(),
        )
        launch_runtime = SimpleNamespace(start_dpi_async=Mock())
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(side_effect=AssertionError("startup snapshot must be resolved in worker")),
            refresh_launch_summary_in_store=Mock(),
        )
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(
                runtime_service=runtime_service,
                launch_runtime=launch_runtime,
            ),
            dependencies=SimpleNamespace(
                presets_feature=presets_feature,
                profile_feature=object(),
            ),
        )
        startup_state = SimpleNamespace(dpi_autostart_initiated=False)

        start_dpi_autostart(
            startup_state,
            runtime_feature=runtime_feature,
            ui_state=object(),
            launch_method="zapret2_mode",
        )

        presets_feature.get_launch_snapshot.assert_not_called()
        runtime_service.mark_start_failed.assert_not_called()
        launch_runtime.start_dpi_async.assert_called_once_with(
            selected_mode=None,
            launch_method="zapret2_mode",
            _startup_autostart=True,
        )
        presets_feature.refresh_launch_summary_in_store.assert_not_called()

    def test_start_flow_defers_request_preparation_to_worker(self) -> None:
        from winws_runtime.runtime import start_flow

        runtime_owner = SimpleNamespace(
            _runtime_feature=SimpleNamespace(),
            _runtime_api=Mock(return_value=object()),
            _runtime_service=Mock(
                return_value=SimpleNamespace(
                    snapshot=Mock(return_value=SimpleNamespace(launch_method="zapret2_mode")),
                    set_busy=Mock(),
                )
            ),
            _begin_runtime_start=Mock(),
            _on_dpi_start_finished=Mock(),
            _pending_launch_warnings=[],
        )

        with (
            patch.object(start_flow, "prepare_start_preflight", return_value=True),
            patch.object(start_flow, "set_runtime_owner_status"),
            patch.object(start_flow, "runtime_owner_status_callback", return_value=Mock()),
            patch.object(start_flow, "start_worker_thread") as start_worker_thread,
        ):
            start_flow.start_dpi_async(
                runtime_owner,
                selected_mode=None,
                launch_method="zapret2_mode",
                startup_autostart=True,
            )

        worker = start_worker_thread.call_args.kwargs["worker"]
        self.assertTrue(worker._prepare_request)
        self.assertIsNone(worker.selected_mode)

    def test_startup_worker_resolves_deferred_startup_preset_snapshot(self) -> None:
        from winws_runtime.runtime.start_workers import PresetLaunchStartWorker

        selected_mode = {
            "is_preset_file": True,
            "preset_path": __file__,
            "name": "Startup preset",
        }
        snapshot = SimpleNamespace(to_selected_mode=Mock(return_value=selected_mode))
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(return_value=snapshot),
        )
        worker = PresetLaunchStartWorker(
            None,
            "zapret2_mode",
            runtime_feature=SimpleNamespace(
                dependencies=SimpleNamespace(presets_feature=presets_feature),
            ),
            runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=False)),
            startup_autostart=True,
        )
        runner = SimpleNamespace(start_from_preset_file=Mock(return_value=True))

        with (
            patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
            patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
        ):
            worker.run()

        presets_feature.get_launch_snapshot.assert_called_once_with(
            "zapret2_mode",
            require_filters=False,
        )
        self.assertEqual(worker.selected_mode, selected_mode)
        runner.start_from_preset_file.assert_called_once()

    def test_preset_autostart_dispatches_without_gui_thread_filter_validation(self) -> None:
        from winws_runtime.runtime.autostart import start_dpi_autostart

        runtime_service = SimpleNamespace(
            snapshot=Mock(
                return_value=SimpleNamespace(
                    phase="autostart_pending",
                    running=False,
                    launch_method="zapret2_mode",
                )
            ),
            mark_start_failed=Mock(),
            mark_stopped=Mock(),
        )
        launch_runtime = SimpleNamespace(start_dpi_async=Mock())
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(side_effect=AssertionError("startup snapshot must be resolved in worker")),
            refresh_launch_summary_in_store=Mock(),
        )
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(
                runtime_service=runtime_service,
                launch_runtime=launch_runtime,
            ),
            dependencies=SimpleNamespace(
                presets_feature=presets_feature,
                profile_feature=object(),
            ),
        )
        startup_state = SimpleNamespace(dpi_autostart_initiated=False)

        start_dpi_autostart(
            startup_state,
            runtime_feature=runtime_feature,
            ui_state=object(),
            launch_method="zapret2_mode",
        )

        presets_feature.get_launch_snapshot.assert_not_called()
        launch_runtime.start_dpi_async.assert_called_once_with(
            selected_mode=None,
            launch_method="zapret2_mode",
            _startup_autostart=True,
        )

    def test_preset_autostart_does_not_refresh_launch_summary_in_gui_thread(self) -> None:
        from winws_runtime.runtime.autostart import start_dpi_autostart

        calls: list[str] = []
        snapshot = SimpleNamespace(to_selected_mode=Mock(return_value={"is_preset_file": True}))
        launch_runtime = SimpleNamespace(start_dpi_async=Mock(side_effect=lambda **_kwargs: calls.append("start")))
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(return_value=snapshot),
            refresh_launch_summary_in_store=Mock(side_effect=lambda **_kwargs: calls.append("refresh_summary")),
        )
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(
                runtime_service=SimpleNamespace(
                    snapshot=Mock(
                        return_value=SimpleNamespace(
                            phase="autostart_pending",
                            running=False,
                            launch_method="zapret2_mode",
                        )
                    ),
                    mark_start_failed=Mock(),
                    mark_stopped=Mock(),
                ),
                launch_runtime=launch_runtime,
            ),
            dependencies=SimpleNamespace(
                presets_feature=presets_feature,
                profile_feature=object(),
            ),
        )
        startup_state = SimpleNamespace(dpi_autostart_initiated=False)

        start_dpi_autostart(
            startup_state,
            runtime_feature=runtime_feature,
            ui_state=object(),
            launch_method="zapret2_mode",
        )

        self.assertEqual(calls, ["start"])
        presets_feature.refresh_launch_summary_in_store.assert_not_called()
        presets_feature.get_launch_snapshot.assert_not_called()

    def test_preset_autostart_reuses_already_running_expected_process(self) -> None:
        from winws_runtime.runtime.autostart import start_dpi_autostart

        runtime_service = SimpleNamespace(
            snapshot=Mock(
                return_value=SimpleNamespace(
                    phase="running",
                    running=True,
                    launch_method="zapret2_mode",
                )
            ),
            mark_start_failed=Mock(),
            mark_stopped=Mock(),
        )
        launch_runtime = SimpleNamespace(start_dpi_async=Mock())
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(),
            refresh_launch_summary_in_store=Mock(),
        )
        runtime_feature = SimpleNamespace(
            objects=SimpleNamespace(
                runtime_service=runtime_service,
                launch_runtime=launch_runtime,
            ),
            dependencies=SimpleNamespace(
                presets_feature=presets_feature,
                profile_feature=object(),
            ),
        )
        startup_state = SimpleNamespace(dpi_autostart_initiated=False)

        start_dpi_autostart(
            startup_state,
            runtime_feature=runtime_feature,
            ui_state=object(),
            launch_method="zapret2_mode",
        )

        runtime_service.snapshot.assert_called_once_with()
        launch_runtime.start_dpi_async.assert_not_called()
        presets_feature.get_launch_snapshot.assert_not_called()
        presets_feature.refresh_launch_summary_in_store.assert_not_called()

    def test_startup_autostart_skips_expensive_preset_prevalidation_in_gui_thread(self) -> None:
        from winws_runtime.flow import start_preparation

        selected_mode = {
            "is_preset_file": True,
            "name": "Пресет",
            "preset_path": __file__,
        }

        with patch.object(
            start_preparation,
            "validate_presets_before_launch",
            side_effect=AssertionError("startup autostart must not prevalidate preset in GUI thread"),
        ):
            request, warnings = start_preparation.prepare_start_request(
                selected_mode,
                "zapret2_mode",
                presets_feature=object(),
                skip_preset_prevalidation=True,
            )

        self.assertEqual(request.selected_mode, selected_mode)
        self.assertEqual(warnings, [])

    def test_start_flow_marks_start_worker_as_startup_autostart(self) -> None:
        from winws_runtime.runtime import start_flow
        selected_mode = {"is_preset_file": True}
        runtime_owner = SimpleNamespace(
            _runtime_feature=SimpleNamespace(),
            _runtime_api=Mock(return_value=object()),
            _runtime_service=Mock(
                return_value=SimpleNamespace(
                    snapshot=Mock(return_value=SimpleNamespace(launch_method="zapret2_mode")),
                    set_busy=Mock(),
                )
            ),
            _begin_runtime_start=Mock(),
            _on_dpi_start_finished=Mock(),
        )

        with (
            patch.object(start_flow, "prepare_start_preflight", return_value=True),
            patch.object(start_flow, "set_runtime_owner_status"),
            patch.object(start_flow, "runtime_owner_status_callback", return_value=Mock()),
            patch.object(start_flow, "start_worker_thread") as start_worker_thread,
        ):
            start_flow.start_dpi_async(
                runtime_owner,
                selected_mode=selected_mode,
                launch_method="zapret2_mode",
                startup_autostart=True,
            )

        worker = start_worker_thread.call_args.kwargs["worker"]
        self.assertTrue(worker._startup_autostart)

    def test_startup_manifest_cache_signature_does_not_read_preset_body(self) -> None:
        import inspect
        from presets.mode_coordinator import PresetModeCoordinator

        source = inspect.getsource(PresetModeCoordinator._selected_manifest_cache_key)

        self.assertIn("path_stat_signature(settings_path)", source)
        self.assertIn("path_stat_signature(preset_path)", source)
        self.assertNotIn("path_cache_signature(settings_path)", source)
        self.assertNotIn("path_cache_signature(preset_path)", source)

    def test_startup_worker_rejects_preset_without_enabled_profiles_before_stop(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "only-skipped.txt"
            preset_path.write_text("--new\n--skip\n--filter-tcp=80\n", encoding="utf-8")

            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=True)),
            )

            result = service.run()

        self.assertFalse(result.success)
        self.assertIn("нет включённых profile", result.error_message)

    def test_startup_worker_skips_pre_stop_validation_when_no_previous_process(self) -> None:
        from winws_runtime.runtime.start_workers import PresetLaunchStartWorker
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")

            worker = PresetLaunchStartWorker(
                {"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                "zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=False)),
                startup_autostart=True,
            )
            runner = SimpleNamespace(start_from_preset_file=Mock(return_value=True))

            with (
                patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
                patch.object(PresetLaunchService, "_validate_preset_before_stop", return_value=True) as validate,
            ):
                worker.run()

        validate.assert_not_called()
        runner.start_from_preset_file.assert_called_once_with(
            str(preset_path),
            "Пресет",
            _stable_start_window_seconds=0.35,
        )

    def test_startup_worker_checks_required_lists_before_preset_launch(self) -> None:
        from winws_runtime.runtime.start_workers import PresetLaunchStartWorker

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")

            worker = PresetLaunchStartWorker(
                {"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                "zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=False)),
                startup_autostart=True,
            )
            runner = SimpleNamespace(start_from_preset_file=Mock(return_value=True))
            calls: list[str] = []

            with (
                patch(
                    "winws_runtime.runtime.preset_launch_service.ensure_required_files_fast",
                    side_effect=lambda *, active_preset_path="": calls.append(active_preset_path) or True,
                ),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
            ):
                worker.run()

        self.assertEqual(calls, [str(preset_path)])
        runner.start_from_preset_file.assert_called_once_with(
            str(preset_path),
            "Пресет",
            _stable_start_window_seconds=0.35,
        )

    def test_startup_worker_uses_short_stable_window_for_autostart(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")

            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=False)),
                startup_autostart=True,
            )
            runner = SimpleNamespace(start_from_preset_file=Mock(return_value=True))

            with (
                patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
            ):
                result = service.run()

        self.assertTrue(result.success)
        kwargs = runner.start_from_preset_file.call_args.kwargs
        self.assertLess(kwargs["_stable_start_window_seconds"], 1.0)

    def test_winws1_retry_preserves_short_startup_stable_window(self) -> None:
        from winws_runtime.runners.zapret1_runner import Winws1StrategyRunner

        runner = object.__new__(Winws1StrategyRunner)
        runner._last_spawn_exit_code = 34
        runner._last_spawn_stderr = "windivert conflict"
        runner._should_retry_transient_windivert_service_error = Mock(return_value=False)
        runner._is_windivert_system_error = Mock(return_value=False)
        runner._is_windivert_conflict_error = Mock(return_value=True)
        runner._start_from_preset_file_locked = Mock(return_value=True)

        self.assertTrue(
            runner._maybe_retry_after_failed_spawn_locked(
                "preset.txt",
                "Preset",
                retry_count=0,
                max_retries=2,
                stable_start_window_seconds=0.35,
            )
        )

        self.assertEqual(
            runner._start_from_preset_file_locked.call_args.kwargs["stable_start_window_seconds"],
            0.35,
        )


if __name__ == "__main__":
    unittest.main()
