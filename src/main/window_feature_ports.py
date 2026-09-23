from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FeatureWindowDeps:
    qt_parent: Any
    startup_state: Any
    close_state: Any
    start_in_tray: bool
    startup_post_init_ready: Any
    tray_window_port: Any
    set_status: Any
    mark_startup_subscription_ready: Any
    mark_stop_and_exit_requested: Any


def build_feature_window_deps(window, *, tray_window_port) -> FeatureWindowDeps:
    def _mark_stop_and_exit_requested() -> None:
        window.close_state.is_exiting = True
        window.close_state.closing_completely = True

    return FeatureWindowDeps(
        qt_parent=window,
        startup_state=window.startup_state,
        close_state=window.close_state,
        start_in_tray=bool(window.start_in_tray),
        startup_post_init_ready=window.startup_post_init_ready,
        tray_window_port=tray_window_port,
        set_status=window.set_status,
        mark_startup_subscription_ready=window.mark_startup_subscription_ready,
        mark_stop_and_exit_requested=_mark_stop_and_exit_requested,
    )


__all__ = ["FeatureWindowDeps", "build_feature_window_deps"]
