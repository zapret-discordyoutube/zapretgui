from __future__ import annotations

from app.feature_assembly import (
    AppFeatureAssemblyDeps,
    PremiumFeatureDeps,
    RuntimeFeatureDeps,
    TrayFeatureDeps,
)
from main.window_feature_ports import FeatureWindowDeps


def initialize_window_holiday_effects(*args, **kwargs):
    from ui.window_appearance_bindings import initialize_window_holiday_effects as _initialize_window_holiday_effects

    return _initialize_window_holiday_effects(*args, **kwargs)


def build_window_feature_deps(window_deps: FeatureWindowDeps, *, appearance_actions) -> AppFeatureAssemblyDeps:
    return AppFeatureAssemblyDeps(
        runtime=RuntimeFeatureDeps(
            qt_parent=window_deps.qt_parent,
            startup_state=window_deps.startup_state,
            mark_stop_and_exit_requested=window_deps.mark_stop_and_exit_requested,
        ),
        premium=PremiumFeatureDeps(
            thread_parent=window_deps.qt_parent,
            set_status=window_deps.set_status,
            init_holiday_effects=lambda effects_allowed: initialize_window_holiday_effects(
                window_deps.qt_parent,
                effects_allowed=effects_allowed,
                appearance_actions=appearance_actions,
            ),
            mark_startup_ready=window_deps.mark_startup_subscription_ready,
        ),
        tray=TrayFeatureDeps(
            window_port=window_deps.tray_window_port,
            startup_state=window_deps.startup_state,
            close_state=window_deps.close_state,
            start_in_tray=bool(window_deps.start_in_tray),
            startup_post_init_ready=window_deps.startup_post_init_ready,
            set_window_opacity=appearance_actions.set_window_opacity,
        ),
    )
