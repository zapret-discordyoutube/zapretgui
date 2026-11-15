"""The ``engine`` interface: every backend hands out the runner of the shared factory (until the Linux runner exists)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from platforms.linux import PLATFORM as LINUX
from platforms.stub import PLATFORM as STUB
from platforms.windows import PLATFORM as WINDOWS


def test_every_backend_has_an_engine() -> None:
    for backend in (WINDOWS, LINUX, STUB):
        for name in ("runner", "current_runner", "reset_runner", "invalidate_runner"):
            assert callable(getattr(backend.engine, name))


def test_engine_calls_reach_the_factory_at_call_time() -> None:
    runner = MagicMock()
    for backend in (WINDOWS, LINUX, STUB):
        with (
            patch("winws_runtime.runners.runner_factory.get_strategy_runner", return_value=runner) as get,
            patch("winws_runtime.runners.runner_factory.get_current_runner", return_value=runner) as current,
            patch("winws_runtime.runners.runner_factory.reset_strategy_runner") as reset,
            patch("winws_runtime.runners.runner_factory.invalidate_strategy_runner") as invalidate,
        ):
            assert backend.engine.runner("C:/z/winws2.exe") is runner
            assert backend.engine.current_runner() is runner
            backend.engine.reset_runner()
            backend.engine.invalidate_runner()
        get.assert_called_once_with("C:/z/winws2.exe")
        current.assert_called_once_with()
        reset.assert_called_once_with()
        invalidate.assert_called_once_with()


def test_neutral_runtime_code_asks_the_platform_for_the_runner() -> None:
    import inspect

    from winws_runtime.health import post_mortem
    from winws_runtime.runtime import control_workers, direct_network, preset_launch_service, sync_shutdown

    for module in (post_mortem, control_workers, direct_network, preset_launch_service, sync_shutdown):
        source = inspect.getsource(module)
        assert "runner_factory" not in source, module.__name__
        assert "engine." in source, module.__name__


def test_direct_network_works_without_a_runner() -> None:
    from winws_runtime.runtime import direct_network

    fake = MagicMock()
    fake.engine.current_runner.return_value = None
    with patch("platforms.current", return_value=fake):
        assert direct_network.direct_network_pause_needed() is False
