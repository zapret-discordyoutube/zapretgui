from log.log import log
from settings.mode import ALL_LAUNCH_METHODS


def start_dpi_autostart(
    startup_state,
    *,
    runtime_feature,
    ui_state,
    launch_method: str | None = None,
) -> None:
    """Запускает DPI autostart через общий runtime-путь."""
    if bool(startup_state.dpi_autostart_initiated):
        log("Автозапуск DPI уже выполнен", "DEBUG")
        return

    startup_state.dpi_autostart_initiated = True

    runtime_snapshot = runtime_feature.objects.runtime_service.snapshot()
    initial_phase = str(getattr(runtime_snapshot, "phase", "") or "").strip().lower()
    if bool(getattr(runtime_snapshot, "running", False)) and initial_phase == "running":
        log("Автозапуск DPI: активный процесс уже опубликован монитором", "INFO")
        return
    if initial_phase != "autostart_pending":
        log("Автозапуск DPI отключён", "INFO")
        _mark_runtime_stopped(runtime_feature)
        return

    resolved_method = str(
        launch_method or getattr(runtime_snapshot, "launch_method", "") or ""
    ).strip().lower()
    if resolved_method not in ALL_LAUNCH_METHODS:
        log(f"Автозапуск не поддерживается для метода: {resolved_method or 'unknown'}", "WARNING")
        _mark_runtime_stopped(runtime_feature)
        return

    log(f"Автозапуск передан в единый DPI runtime-путь: {resolved_method}", "INFO")
    runtime_feature.objects.launch_runtime.start_dpi_async(
        selected_mode=None,
        launch_method=resolved_method,
        _startup_autostart=True,
    )


def _mark_runtime_stopped(runtime_feature) -> None:
    runtime_feature.objects.runtime_service.mark_stopped(clear_error=True)


def _mark_runtime_failed(runtime_feature, message: str) -> None:
    runtime_feature.objects.runtime_service.mark_start_failed(str(message or "").strip())
