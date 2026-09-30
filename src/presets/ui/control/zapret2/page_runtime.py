from __future__ import annotations

from presets.ui.control import control_runtime
from presets.ui.control.control_runtime import ControlStatusPlan


def create_refresh_runtime():
    from presets.ui.control.refresh_runtime_state import create_refresh_runtime as _create_refresh_runtime

    return _create_refresh_runtime()


def build_additional_settings_state(state):
    from presets.ui.control.additional_settings_runtime import build_additional_settings_state as _build_state

    return _build_state(state)


def build_status_plan(*, state, last_error: str, language: str) -> ControlStatusPlan:
    return control_runtime.build_status_plan_for(
        text_prefix="page.winws2_control",
        state=state,
        last_error=last_error,
        language=language,
        autostart_description="Подготавливаем стартовый запуск выбранного пресета",
    )
