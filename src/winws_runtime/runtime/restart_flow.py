from __future__ import annotations

from PyQt6.QtCore import QTimer

from log.log import log

from settings.mode import is_preset_launch_method, normalize_launch_method

from .discord_restart_flow import maybe_restart_discord_after_runtime_apply
from .lifecycle_feedback import show_launch_error_top
from .status_feedback import runtime_owner_status_callback, set_runtime_owner_status
from .thread_runtime import start_worker_thread
from .control_workers import PresetSwitchWorker


def _active_runtime_owner(runtime_owner) -> tuple[bool, str]:
    snapshot = runtime_owner._runtime_service().snapshot()
    phase = str(getattr(snapshot, "phase", "") or "").strip().lower()
    running = bool(getattr(snapshot, "running", False)) and phase == "running"
    return (
        running,
        normalize_launch_method(
            getattr(snapshot, "launch_method", ""),
            default="",
        ),
    )


def _redirect_preset_switch_if_owner_differs(
    runtime_owner,
    target_launch_method: str,
    *,
    completed_generation: int = 0,
) -> bool:
    """Единое правило владения: fast switch допустим только внутри режима."""
    active_running, active_launch_method = _active_runtime_owner(runtime_owner)
    if not active_running:
        return False
    if active_launch_method == target_launch_method:
        return False

    _cancel_debounced_presets_switch(runtime_owner)
    if completed_generation > 0:
        runtime_owner._presets_switch_completed_generation = max(
            int(runtime_owner._presets_switch_completed_generation or 0),
            int(completed_generation),
        )
    runtime_owner._runtime_service().set_busy(False)
    log(
        "Быстрое переключение preset запрещено для другого владельца процесса: "
        f"активный режим '{active_launch_method or 'unknown'}', "
        f"целевой режим '{target_launch_method}'. Выполняем полный stop+start",
        "WARNING",
    )
    runtime_owner.restart_dpi_async(
        force_full_stop=True,
        target_launch_method=target_launch_method,
    )
    return True


def _schedule_pending_presets_switch_retry(runtime_owner) -> None:
    """Повтор для отложенного pending switch.

    QThread ещё числится isRunning() короткое время после finished-сигнала,
    а singleShot(0) из finish-хендлера успевает выстрелить раньше. Без
    повтора pending-поколение теряется навсегда и busy («Применяем
    пресет...») не снимается.
    """
    if getattr(runtime_owner, "_presets_switch_wait_queued", False):
        return
    runtime_owner._presets_switch_wait_queued = True

    def _retry() -> None:
        runtime_owner._presets_switch_wait_queued = False
        process_pending_presets_switch(runtime_owner)

    QTimer.singleShot(150, _retry)


def process_pending_presets_switch(runtime_owner) -> None:
    target_generation = int(runtime_owner._presets_switch_requested_generation or 0)
    if target_generation <= int(runtime_owner._presets_switch_completed_generation or 0):
        return

    runtime_snapshot = runtime_owner._runtime_service().snapshot()
    launch_method = str(
        runtime_owner._presets_switch_method
        or getattr(runtime_snapshot, "launch_method", "")
        or ""
    ).strip().lower()
    if not is_preset_launch_method(launch_method):
        runtime_owner._runtime_service().set_busy(False)
        return

    try:
        if runtime_owner._presets_switch_thread and runtime_owner._presets_switch_thread.isRunning():
            _schedule_pending_presets_switch_retry(runtime_owner)
            return
    except RuntimeError:
        runtime_owner._presets_switch_thread = None

    try:
        if runtime_owner._dpi_start_thread and runtime_owner._dpi_start_thread.isRunning():
            log(
                f"Preset mode switch отложен: основной start pipeline ещё идёт, поколение {target_generation}",
                "DEBUG",
            )
            _schedule_pending_presets_switch_retry(runtime_owner)
            return
    except RuntimeError:
        runtime_owner._dpi_start_thread = None

    try:
        if runtime_owner._dpi_stop_thread and runtime_owner._dpi_stop_thread.isRunning():
            log(
                f"Preset mode switch отложен: stop pipeline ещё идёт, поколение {target_generation}",
                "DEBUG",
            )
            _schedule_pending_presets_switch_retry(runtime_owner)
            return
    except RuntimeError:
        runtime_owner._dpi_stop_thread = None

    if not runtime_owner.is_running():
        log("Preset mode switch пропущен: DPI уже не запущен", "DEBUG")
        runtime_owner._presets_switch_completed_generation = target_generation
        runtime_owner._runtime_service().set_busy(False)
        return

    if _redirect_preset_switch_if_owner_differs(
        runtime_owner,
        launch_method,
        completed_generation=target_generation,
    ):
        return

    start_worker_thread(
        runtime_owner,
        thread_attr="_presets_switch_thread",
        worker_attr="_presets_switch_worker",
        worker=PresetSwitchWorker(
            runtime_owner._runtime_feature.dependencies.presets_feature,
            launch_method,
            target_generation,
            lambda generation: generation == runtime_owner._presets_switch_requested_generation,
        ),
        finished_slot=runtime_owner._on_presets_switch_finished,
        progress_slot=runtime_owner_status_callback(runtime_owner),
        cleanup_log_label="preset mode switch thread",
    )


def _cancel_debounced_presets_switch(runtime_owner) -> None:
    try:
        timer = getattr(runtime_owner, "_presets_switch_debounce_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
    except Exception:
        pass
    try:
        runtime_owner._presets_switch_debounce_method = ""
    except Exception:
        pass


def _fire_debounced_presets_switch(runtime_owner) -> None:
    method = str(getattr(runtime_owner, "_presets_switch_debounce_method", "") or "").strip().lower()
    _cancel_debounced_presets_switch(runtime_owner)
    if method:
        switch_presets_async(runtime_owner, method)


def _schedule_debounced_presets_switch(runtime_owner, method: str, delay_ms: int) -> None:
    runtime_owner._presets_switch_debounce_method = method
    timer = getattr(runtime_owner, "_presets_switch_debounce_timer", None)
    if timer is None:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: _fire_debounced_presets_switch(runtime_owner))
        runtime_owner._presets_switch_debounce_timer = timer
    timer.start(max(0, int(delay_ms)))
    log(
        f"Preset mode switch отложен на {max(0, int(delay_ms))}мс, ждём последние изменения ({method})",
        "DEBUG",
    )


def switch_presets_async(runtime_owner, launch_method: str | None = None, *, delay_ms: int = 0) -> None:
    runtime_snapshot = runtime_owner._runtime_service().snapshot()
    current_method = getattr(runtime_snapshot, "launch_method", "")
    method = str(launch_method or current_method or "").strip().lower()
    if not is_preset_launch_method(method):
        runtime_owner.restart_dpi_async()
        return

    if _redirect_preset_switch_if_owner_differs(runtime_owner, method):
        return

    if int(delay_ms or 0) > 0:
        _schedule_debounced_presets_switch(runtime_owner, method, int(delay_ms))
        return

    _cancel_debounced_presets_switch(runtime_owner)
    runtime_owner._presets_switch_method = method
    runtime_owner._presets_switch_requested_generation += 1
    runtime_owner._runtime_service().set_busy(True, "Применяем пресет...")
    log(
        f"Preset mode switch запросили, актуальное поколение {runtime_owner._presets_switch_requested_generation} ({method})",
        "INFO",
    )
    process_pending_presets_switch(runtime_owner)


def process_pending_restart_request(runtime_owner) -> None:
    target_generation = int(runtime_owner._restart_request_generation or 0)
    if target_generation <= int(runtime_owner._restart_completed_generation or 0):
        return

    force_full_stop = int(runtime_owner._restart_force_stop_generation or 0) == target_generation

    try:
        if runtime_owner._dpi_start_thread and runtime_owner._dpi_start_thread.isRunning():
            log(
                f"Перезапуск DPI отложен: запуск ещё идёт, актуальное поколение {target_generation}",
                "DEBUG",
            )
            runtime_owner._schedule_pending_restart_retry()
            return
    except RuntimeError:
        runtime_owner._dpi_start_thread = None

    try:
        if runtime_owner._dpi_stop_thread and runtime_owner._dpi_stop_thread.isRunning():
            runtime_owner._restart_pending_stop_generation = target_generation
            log(
                f"Перезапуск DPI отложен: остановка ещё идёт, актуальное поколение {target_generation}",
                "DEBUG",
            )
            runtime_owner._schedule_pending_restart_retry()
            return
    except RuntimeError:
        runtime_owner._dpi_stop_thread = None

    try:
        if runtime_owner._presets_switch_thread and runtime_owner._presets_switch_thread.isRunning():
            log(
                f"Перезапуск DPI отложен: preset mode switch ещё идёт, актуальное поколение {target_generation}",
                "DEBUG",
            )
            runtime_owner._schedule_pending_restart_retry()
            return
    except RuntimeError:
        runtime_owner._presets_switch_thread = None

    current_running = bool(runtime_owner.is_running())
    if current_running or force_full_stop:
        runtime_owner._restart_pending_stop_generation = target_generation
        if force_full_stop and not current_running:
            log(
                f"Перезапуск DPI: смена режима требует полного stop+cleanup, актуальное поколение {target_generation}",
                "INFO",
            )
        else:
            log(
                f"Перезапуск DPI: сначала останавливаем текущий процесс, актуальное поколение {target_generation}",
                "INFO",
            )
        runtime_owner.stop_dpi_async(
            force_cleanup=force_full_stop,
            cleanup_services=False,
        )
        return

    runtime_owner._restart_active_start_generation = target_generation
    target_launch_method = normalize_launch_method(
        getattr(runtime_owner, "_restart_target_launch_method", ""),
        default="",
    )
    log(
        "Перезапуск DPI: запускаем актуальный выбранный preset, "
        f"поколение {target_generation}, режим {target_launch_method or 'current'}",
        "INFO",
    )
    runtime_owner.start_dpi_async(launch_method=target_launch_method or None)


def handle_presets_switch_finished(runtime_owner, success, error_message, generation, launch_method, skipped_as_stale) -> None:
    try:
        requested_generation = int(runtime_owner._presets_switch_requested_generation or 0)
        finished_generation = int(generation or 0)
        runtime_owner._presets_switch_completed_generation = max(
            int(runtime_owner._presets_switch_completed_generation or 0),
            finished_generation,
        )

        stale_finish = bool(skipped_as_stale) or finished_generation < requested_generation
        if stale_finish:
            worker = getattr(runtime_owner, "_presets_switch_worker", None)
            pid = getattr(worker, "started_pid", None)
            if success and isinstance(pid, int):
                # Устаревшее поколение уже успело переключить процесс: без
                # фиксации snapshot держит PID убитого процесса, пока следующее
                # поколение не завершится. busy не снимаем — pending ещё в полёте.
                runtime_owner._mark_runtime_running(pid=pid)
            log(
                f"Preset mode switch поколения {generation} пропущен как устаревший ({launch_method})",
                "DEBUG",
            )
            return

        runtime_owner._runtime_service().set_busy(False)

        if success:
            worker = getattr(runtime_owner, "_presets_switch_worker", None)
            pid = getattr(worker, "started_pid", None)
            runtime_owner._mark_runtime_running(pid=pid if isinstance(pid, int) else None)
            log(
                f"Preset mode switch успешно завершён, поколение {generation} ({launch_method})",
                "INFO",
            )
            maybe_restart_discord_after_runtime_apply(runtime_owner, skip_first_start=False)
            if int(runtime_owner._presets_switch_requested_generation or 0) <= int(runtime_owner._presets_switch_completed_generation or 0):
                set_runtime_owner_status(runtime_owner, "✅ Пресет успешно применён")
        else:
            log(
                f"Ошибка preset mode switch, поколение {generation} ({launch_method}): {error_message}",
                "❌ ERROR",
            )
            set_runtime_owner_status(runtime_owner, f"❌ Ошибка переключения пресета: {error_message}")
            show_launch_error_top(runtime_owner, error_message)
    finally:
        if runtime_owner._presets_switch_requested_generation > runtime_owner._presets_switch_completed_generation:
            QTimer.singleShot(0, runtime_owner._process_pending_presets_switch)


def restart_dpi_async(
    runtime_owner,
    *,
    force_full_stop: bool = False,
    target_launch_method: str | None = None,
) -> None:
    normalized_target_method = normalize_launch_method(
        target_launch_method,
        default="",
    )
    runtime_owner._restart_request_generation += 1
    runtime_owner._restart_target_launch_method = normalized_target_method
    if force_full_stop:
        runtime_owner._restart_force_stop_generation = int(runtime_owner._restart_request_generation)
    log(
        "Перезапуск DPI запросили, "
        f"актуальное поколение {runtime_owner._restart_request_generation}, "
        f"целевой режим {normalized_target_method or 'current'}",
        "INFO",
    )
    process_pending_restart_request(runtime_owner)
