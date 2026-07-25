from __future__ import annotations

from settings.mode import is_preset_launch_method, normalize_launch_method


def transition_pipeline_in_progress(runtime_owner, launch_method: str | None = None) -> bool:
    method = normalize_launch_method(launch_method, default="")

    try:
        if runtime_owner._dpi_start_thread and runtime_owner._dpi_start_thread.isRunning():
            return True
    except RuntimeError:
        runtime_owner._dpi_start_thread = None

    try:
        if runtime_owner._dpi_stop_thread and runtime_owner._dpi_stop_thread.isRunning():
            return True
    except RuntimeError:
        runtime_owner._dpi_stop_thread = None

    try:
        if runtime_owner._presets_switch_thread and runtime_owner._presets_switch_thread.isRunning():
            if not method or is_preset_launch_method(method):
                return True
    except RuntimeError:
        runtime_owner._presets_switch_thread = None

    if int(runtime_owner._restart_request_generation or 0) > int(runtime_owner._restart_completed_generation or 0):
        return True
    if int(runtime_owner._restart_active_start_generation or 0) > 0:
        return True
    if int(runtime_owner._restart_pending_stop_generation or 0) > 0:
        return True
    if int(runtime_owner._presets_switch_requested_generation or 0) > int(runtime_owner._presets_switch_completed_generation or 0):
        if not method or is_preset_launch_method(method):
            return True

    return False


def is_running(runtime_owner) -> bool:
    """Читает только опубликованное состояние, без runner/WinAPI probes."""
    try:
        snapshot = runtime_owner._runtime_service().snapshot()
        phase = str(snapshot.phase or "").strip().lower()
        return bool(snapshot.running) and phase == "running"
    except Exception:
        return False
