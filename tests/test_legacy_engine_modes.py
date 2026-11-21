"""Zapret 1 and the Orchestra exist only where their engines do (``Cap.LEGACY_ENGINE_MODES``: Windows)."""
from __future__ import annotations

from platforms import Cap
from platforms.stub import make_platform
from settings import mode
from ui.onboarding.steps import ALL_TOUR_STEPS, tour_steps_for

_WINDOWS = make_platform(name="windows", capabilities=frozenset({Cap.LEGACY_ENGINE_MODES}))
_LINUX = make_platform(name="linux", capabilities=frozenset())


def test_a_saved_legacy_mode_becomes_zapret2_where_it_has_no_engine(monkeypatch) -> None:
    monkeypatch.setattr("platforms.current", lambda: _LINUX)
    for saved in (mode.ZAPRET1_MODE, mode.ORCHESTRA_MODE, "ORCHESTRA"):
        assert mode.normalize_launch_method(saved) == mode.ZAPRET2_MODE
    assert mode.normalize_launch_method(mode.ZAPRET2_MODE) == mode.ZAPRET2_MODE
    assert mode.normalize_launch_method("nonsense") == mode.DEFAULT_LAUNCH_METHOD


def test_windows_keeps_every_mode(monkeypatch) -> None:
    monkeypatch.setattr("platforms.current", lambda: _WINDOWS)
    for saved in mode.ALL_LAUNCH_METHODS:
        assert mode.normalize_launch_method(saved) == saved


def test_the_tour_has_no_mode_steps_without_the_modes() -> None:
    keys = {step.key for step in tour_steps_for(_LINUX)}
    assert not keys & {"dpi_mode", "dpi_modes"}
    assert {"dpi_mode", "dpi_modes"} <= {step.key for step in tour_steps_for(_WINDOWS)}
    assert {"dpi_mode", "dpi_modes"} <= {step.key for step in ALL_TOUR_STEPS}
