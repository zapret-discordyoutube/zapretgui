from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class _RuntimeApi:
    """Остаток процессов пропадает после остановки runner-ом или полным кругом."""

    def __init__(self, *, residual: bool) -> None:
        self.residual = bool(residual)
        self.stop_all_processes = Mock(side_effect=self._stop_all)

    def has_residual_processes(self, silent: bool = False) -> bool:
        return self.residual

    def _stop_all(self) -> bool:
        self.residual = False
        return True

    def cleanup_windivert_service(self) -> bool:
        return True


def _runtime_feature(runtime_api) -> SimpleNamespace:
    runtime_service = SimpleNamespace(snapshot=lambda: SimpleNamespace(launch_method="zapret2_mode"))
    return SimpleNamespace(
        objects=SimpleNamespace(runtime_service=runtime_service, launch_runtime_api=runtime_api),
        dependencies=SimpleNamespace(orchestra_feature=None),
    )


def _runner_that_kills(runtime_api) -> SimpleNamespace:
    def _stop(**_kwargs) -> bool:
        runtime_api.residual = False
        return True

    return SimpleNamespace(stop=Mock(side_effect=_stop))


class SyncShutdownPrelaunchTests(unittest.TestCase):
    def _shutdown(self, runtime_api, runner, **kwargs):
        from winws_runtime.runtime.sync_shutdown import shutdown_runtime_sync

        with (
            patch("winws_runtime.runners.runner_factory.get_current_runner", return_value=runner),
            patch("winws_runtime.runners.runner_factory.invalidate_strategy_runner") as invalidate,
        ):
            result = shutdown_runtime_sync(
                runtime_feature=_runtime_feature(runtime_api),
                include_cleanup=False,
                update_runtime_state=False,
                **kwargs,
            )
        return result, invalidate

    def test_full_stop_round_skipped_when_runner_already_killed_everything(self) -> None:
        runtime_api = _RuntimeApi(residual=True)

        result, _invalidate = self._shutdown(runtime_api, _runner_that_kills(runtime_api))

        runtime_api.stop_all_processes.assert_not_called()
        self.assertFalse(result.still_running)
        self.assertTrue(result.had_running_processes)

    def test_full_stop_round_runs_when_processes_remain(self) -> None:
        runtime_api = _RuntimeApi(residual=True)

        result, _invalidate = self._shutdown(runtime_api, runner=None)

        runtime_api.stop_all_processes.assert_called_once_with()
        self.assertFalse(result.still_running)

    def test_runner_is_dropped_by_default(self) -> None:
        runtime_api = _RuntimeApi(residual=True)

        _result, invalidate = self._shutdown(runtime_api, _runner_that_kills(runtime_api))

        invalidate.assert_called_once_with()

    def test_keep_runner_leaves_runner_for_next_start(self) -> None:
        runtime_api = _RuntimeApi(residual=True)

        _result, invalidate = self._shutdown(runtime_api, _runner_that_kills(runtime_api), keep_runner=True)

        invalidate.assert_not_called()

    def test_prelaunch_stop_keeps_runner(self) -> None:
        from winws_runtime.runtime.preset_launch_service import PresetLaunchService

        with tempfile.TemporaryDirectory() as tmp_dir:
            preset_path = Path(tmp_dir) / "ready.txt"
            preset_path.write_text("--new\n--filter-tcp=80\n", encoding="utf-8")
            service = PresetLaunchService(
                selected_mode={"is_preset_file": True, "preset_path": str(preset_path), "name": "Пресет"},
                launch_method="zapret2_mode",
                runtime_feature=SimpleNamespace(),
                runtime_api=SimpleNamespace(has_residual_processes=Mock(return_value=True)),
            )
            with patch(
                "winws_runtime.runtime.preset_launch_service.shutdown_runtime_sync",
                return_value=SimpleNamespace(still_running=False),
            ) as shutdown:
                service._stop_previous_process_if_needed(skip_stop=False, process_running=True)

        self.assertTrue(shutdown.call_args.kwargs["keep_runner"])
        # Остановка внутри перезапуска драйвер не выгружает.
        self.assertFalse(shutdown.call_args.kwargs["include_cleanup"])
        self.assertFalse(shutdown.call_args.kwargs["cleanup_services"])

    def test_prelaunch_stop_has_no_blind_pauses(self) -> None:
        # Остановка подтверждается выходом процесса; пауз «на всякий случай»
        # после неё быть не должно.
        import inspect

        from winws_runtime.runtime import preset_launch_service

        source = inspect.getsource(preset_launch_service)
        self.assertNotIn("time.sleep", source)
        self.assertFalse(hasattr(preset_launch_service, "time"))


if __name__ == "__main__":
    unittest.main()
