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


class PresetLaunchServiceTests(unittest.TestCase):
    def test_start_worker_delegates_launch_to_service(self) -> None:
        from winws_runtime.runtime import start_workers
        from winws_runtime.runtime.start_workers import PresetLaunchStartWorker

        selected_mode = {
            "is_preset_file": True,
            "preset_path": __file__,
            "name": "Пресет",
        }
        result = SimpleNamespace(
            success=True,
            error_message="",
            selected_mode=selected_mode,
        )
        service = SimpleNamespace(run=Mock(return_value=result))
        runtime_feature = SimpleNamespace()
        runtime_api = SimpleNamespace(expected_exe_path="winws2.exe")
        worker = PresetLaunchStartWorker(
            selected_mode,
            "zapret2_mode",
            runtime_feature=runtime_feature,
            runtime_api=runtime_api,
            startup_autostart=True,
        )
        finished: list[tuple[bool, str]] = []
        worker.finished.connect(lambda success, error: finished.append((success, error)))

        with patch.object(start_workers, "PresetLaunchService", return_value=service) as service_cls:
            worker.run()

        service_cls.assert_called_once()
        self.assertEqual(service_cls.call_args.kwargs["selected_mode"], selected_mode)
        self.assertEqual(service_cls.call_args.kwargs["launch_method"], "zapret2_mode")
        self.assertIs(service_cls.call_args.kwargs["runtime_feature"], runtime_feature)
        self.assertIs(service_cls.call_args.kwargs["runtime_api"], runtime_api)
        self.assertTrue(service_cls.call_args.kwargs["startup_autostart"])
        service.run.assert_called_once()
        self.assertEqual(worker.selected_mode, selected_mode)
        self.assertEqual(finished, [(True, "")])

    def test_service_rejects_invalid_preset_before_stopping_previous_process(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "only-skipped.txt"
            preset_path.write_text("--new\n--skip\n--filter-tcp=80\n", encoding="utf-8")
            runtime_api = SimpleNamespace(
                expected_exe_path="winws2.exe",
                has_residual_processes=Mock(return_value=True),
            )
            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=runtime_api,
            )

            with patch(
                "winws_runtime.runtime.preset_launch_service.shutdown_runtime_sync",
                side_effect=AssertionError("invalid preset must not stop previous process"),
            ) as shutdown:
                result = service.run()

        self.assertFalse(result.success)
        self.assertIn("нет включённых profile", result.error_message)
        shutdown.assert_not_called()

    def test_service_prepares_lists_before_validating_preset_with_running_process(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        events: list[object] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text(
                "--new\n--wf-tcp-out=443\n--filter-tcp=443\n--hostlist=lists/tiktok.txt\n",
                encoding="utf-8",
            )
            runtime_api = SimpleNamespace(
                expected_exe_path="winws2.exe",
                has_residual_processes=Mock(return_value=True),
            )
            runner = SimpleNamespace(
                validate_preset_file=Mock(side_effect=lambda _path: events.append("validate") or (True, "")),
                start_from_preset_file=Mock(return_value=True),
            )
            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=runtime_api,
            )

            with (
                patch(
                    "winws_runtime.runtime.preset_launch_service.ensure_required_files_fast",
                    side_effect=lambda *, active_preset_path="": (
                        events.append(("prepare", active_preset_path)) or True
                    ),
                ),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
                patch(
                    "winws_runtime.runtime.preset_launch_service.shutdown_runtime_sync",
                    return_value=SimpleNamespace(still_running=False),
                ),
            ):
                result = service.run()

        self.assertTrue(result.success)
        self.assertEqual(events[:2], [("prepare", str(preset_path)), "validate"])

    def test_service_uses_short_stable_window_for_startup_autostart(self) -> None:
        from winws_runtime.runtime.preset_launch_service import (
            STARTUP_AUTOSTART_STABLE_WINDOW_SECONDS,
            PresetLaunchService,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")
            runner = SimpleNamespace(start_from_preset_file=Mock(return_value=True))
            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=False)),
                startup_autostart=True,
            )

            with (
                patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
            ):
                result = service.run()

        self.assertTrue(result.success)
        runner.start_from_preset_file.assert_called_once_with(
            str(preset_path),
            "Пресет",
            _stable_start_window_seconds=STARTUP_AUTOSTART_STABLE_WINDOW_SECONDS,
        )

    def test_engine_left_running_is_handed_over_without_a_gap(self) -> None:
        """Движок от прежней версии: свой стартует рядом, прежний гасится после — не «стоп, потом старт»."""
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text(
                "--new\n--wf-tcp-out=443\n--filter-tcp=443\n--hostlist=lists/tiktok.txt\n", encoding="utf-8"
            )
            runner = SimpleNamespace(
                validate_preset_file=Mock(return_value=(True, "")),
                adopt_running_engine=Mock(return_value=True),
                switch_preset_file_fast=Mock(return_value=True),
                stop_extra_adopted_engines=Mock(),
                start_from_preset_file=Mock(return_value=True),
                get_runner_state_snapshot=Mock(return_value=SimpleNamespace(pid=4242)),
            )
            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(expected_exe_path="winws2.exe", has_residual_processes=Mock(return_value=True)),
                startup_autostart=True,
            )
            with (
                patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
                patch("winws_runtime.runtime.preset_launch_service.shutdown_runtime_sync") as shutdown,
            ):
                result = service.run()

        self.assertTrue(result.success)
        shutdown.assert_not_called()  # прежний движок не останавливали заранее
        runner.switch_preset_file_fast.assert_called_once_with(str(preset_path), "Пресет")
        runner.start_from_preset_file.assert_not_called()
        runner.stop_extra_adopted_engines.assert_called_once_with()
        self.assertEqual(service.started_pid, 4242)

    def test_engine_that_cannot_be_adopted_is_stopped_and_started_as_before(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text(
                "--new\n--wf-tcp-out=443\n--filter-tcp=443\n--hostlist=lists/tiktok.txt\n", encoding="utf-8"
            )
            runner = SimpleNamespace(
                validate_preset_file=Mock(return_value=(True, "")),
                adopt_running_engine=Mock(return_value=False),  # например, Zapret 1 или чужой процесс
                switch_preset_file_fast=Mock(),
                start_from_preset_file=Mock(return_value=True),
            )
            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(expected_exe_path="winws2.exe", has_residual_processes=Mock(return_value=True)),
            )
            with (
                patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
                patch(
                    "winws_runtime.runtime.preset_launch_service.shutdown_runtime_sync",
                    return_value=SimpleNamespace(still_running=False),
                ) as shutdown,
            ):
                result = service.run()

        self.assertTrue(result.success)
        shutdown.assert_called_once()
        runner.switch_preset_file_fast.assert_not_called()
        runner.start_from_preset_file.assert_called_once()


class PresetReplacedDuringLaunchTests(unittest.TestCase):
    """Первый запуск после обновления: копия встроенного пресета заменяется, пока автозапуск уже взял её путь."""

    NAME = "Default v1 (game filter).txt"

    def _service(self, tmp: Path, *, snapshot=None, snapshot_error=None, selected_name=None):
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        stale = tmp / "winws2" / self.NAME  # файла нет: копию заменили встроенным и удалили
        presets_feature = SimpleNamespace(
            get_launch_snapshot=Mock(side_effect=snapshot_error) if snapshot_error else Mock(return_value=snapshot)
        )
        service = PresetLaunchService(
            selected_mode={"is_preset_file": True, "preset_path": str(stale), "name": selected_name or "Default v1"},
            launch_method="zapret2_mode",
            runtime_feature=SimpleNamespace(dependencies=SimpleNamespace(presets_feature=presets_feature)),
            runtime_api=SimpleNamespace(expected_exe_path="winws2.exe", has_residual_processes=Mock(return_value=True)),
        )
        return service, stale, presets_feature

    def _snapshot(self, tmp: Path, file_name: str):
        builtin = tmp / "winws2_builtin" / file_name
        builtin.parent.mkdir(parents=True, exist_ok=True)
        builtin.write_text("--new\n--wf-tcp-out=443\n--filter-tcp=443\n--hostlist=lists/a.txt\n", encoding="utf-8")
        mode = {"is_preset_file": True, "name": "Default v1", "preset_path": str(builtin)}
        return SimpleNamespace(preset_file_name=file_name, preset_path=builtin, to_selected_mode=lambda: dict(mode)), builtin

    def test_missing_copy_is_replaced_by_the_same_builtin_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, builtin = self._snapshot(Path(tmp), self.NAME)
            service, stale, feature = self._service(Path(tmp), snapshot=snapshot)

            recovered = service._recover_replaced_preset_path(str(stale))

            self.assertEqual(recovered, str(builtin))
            self.assertEqual(service.selected_mode["preset_path"], str(builtin))
            self.assertEqual(service.selected_mode["name"], "Default v1")
            feature.get_launch_snapshot.assert_called_once()

    def test_another_selected_preset_is_never_substituted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, _builtin = self._snapshot(Path(tmp), "Other preset.txt")
            service, stale, _feature = self._service(Path(tmp), snapshot=snapshot)

            self.assertEqual(service._recover_replaced_preset_path(str(stale)), str(stale))
            self.assertEqual(service.selected_mode["preset_path"], str(stale))

    def test_failure_to_select_again_keeps_the_old_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, stale, _feature = self._service(Path(tmp), snapshot_error=RuntimeError("нет выбора"))

            with patch("winws_runtime.runtime.preset_launch_service.log"):
                self.assertEqual(service._recover_replaced_preset_path(str(stale)), str(stale))

    def test_existing_file_is_not_looked_up_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, builtin = self._snapshot(Path(tmp), self.NAME)
            service, _stale, feature = self._service(Path(tmp), snapshot=snapshot)

            self.assertEqual(service._recover_replaced_preset_path(str(builtin)), str(builtin))
            feature.get_launch_snapshot.assert_not_called()

    def test_launch_with_running_engine_survives_the_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot, builtin = self._snapshot(Path(tmp), self.NAME)
            service, _stale, _feature = self._service(Path(tmp), snapshot=snapshot)
            runner = SimpleNamespace(
                validate_preset_file=Mock(return_value=(True, "")),
                start_from_preset_file=Mock(return_value=True),
            )

            with (
                patch("winws_runtime.runtime.preset_launch_service.ensure_required_files_fast", return_value=True),
                patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner),
                patch(
                    "winws_runtime.runtime.preset_launch_service.shutdown_runtime_sync",
                    return_value=SimpleNamespace(still_running=False),
                ),
            ):
                result = service.run()

        self.assertTrue(result.success, result.error_message)
        self.assertEqual(runner.start_from_preset_file.call_args.args[0], str(builtin))
        self.assertEqual(result.selected_mode["preset_path"], str(builtin))


if __name__ == "__main__":
    unittest.main()
