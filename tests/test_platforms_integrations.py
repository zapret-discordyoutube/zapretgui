"""Windows-only integrations are capabilities: Defender, MAX blocker, antivirus probe, network reset."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from platforms import Cap
from platforms.linux import PLATFORM as LINUX
from platforms.stub import PLATFORM as STUB
from platforms.windows import PLATFORM as WINDOWS

INTEGRATIONS = (Cap.DEFENDER, Cap.MAX_BLOCKER, Cap.ANTIVIRUS_PROBE, Cap.NETWORK_RESET)


def test_windows_has_every_integration_and_the_others_have_none() -> None:
    for capability in INTEGRATIONS:
        assert WINDOWS.has(capability)
        assert not LINUX.has(capability) and not STUB.has(capability)


def _without(capability: str):
    fake = MagicMock()
    fake.has.side_effect = lambda name: name != capability
    return fake


@pytest.mark.parametrize(
    ("function", "capability", "argument"),
    [("set_defender_disabled", Cap.DEFENDER, True), ("set_max_block_enabled", Cap.MAX_BLOCKER, True)],
)
def test_switches_refuse_without_the_capability_and_touch_nothing(function, capability, argument) -> None:
    from program_settings import commands

    with patch("platforms.current", return_value=_without(capability)):
        result = getattr(commands, function)(argument)
    assert result.level == "warning" and "Windows" in result.content
    assert result.revert_checked is None


def test_defender_switch_still_reaches_the_manager_where_the_capability_exists() -> None:
    from program_settings import commands

    manager = MagicMock()
    manager.disable_defender.return_value = (True, 3)
    with (
        patch("platforms.current", return_value=MagicMock(has=lambda name: True)),
        patch("windows_features.defender_manager.WindowsDefenderManager", return_value=manager),
        patch("windows_features.defender_manager.set_defender_disabled"),
    ):
        result = commands.set_defender_disabled(True)
    manager.disable_defender.assert_called_once()
    assert result.level == "success"


def test_without_the_antivirus_probe_nothing_is_detected_and_no_cooldown_is_added() -> None:
    from blockcheck.strategy_search import environment
    from diagnostics import system_state
    from winws_runtime.runtime import system_ops

    with patch("platforms.current", return_value=_without(Cap.ANTIVIRUS_PROBE)):
        assert system_state._read_antivirus() == ""
        assert system_ops._is_kaspersky_present_strict() is False
        assert environment.strategy_pause_seconds() == 0.0


def test_with_the_antivirus_probe_the_windows_detector_decides() -> None:
    from blockcheck.strategy_search import environment

    with (
        patch("platforms.current", return_value=MagicMock(has=lambda name: True)),
        patch("utils.antivirus_probe.is_kaspersky_present", return_value=True),
    ):
        assert environment.strategy_pause_seconds() == environment.KASPERSKY_STRATEGY_COOLDOWN_SECONDS
