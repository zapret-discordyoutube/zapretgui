from __future__ import annotations

from log.log import log
from settings.mode import normalize_launch_method
from winws_runtime.flow.start_preparation import resolve_method_name, resolve_mode_name

from .conflict_flow import handle_conflicting_processes_before_start
from .lifecycle_feedback import show_launch_error_top
from .status_feedback import runtime_owner_status_callback, set_runtime_owner_status
from .start_workers import PresetLaunchStartWorker
from .thread_runtime import start_worker_thread


def fail_start_preparation(runtime_owner, message: str) -> None:
    text = str(message or "").strip() or "Не удалось подготовить запуск DPI"
    log(f"Ошибка подготовки запуска: {text}", "❌ ERROR")
    set_runtime_owner_status(runtime_owner, f"❌ {text}")
    show_launch_error_top(runtime_owner, text)
    runtime_owner._mark_runtime_failed(text)


def prepare_start_preflight(
    runtime_owner,
    *,
    selected_mode=None,
    launch_method=None,
    skip_conflict_prompt: bool = False,
) -> bool:
    """Выполняет раннюю проверку перед построением launch request."""
    try:
        if runtime_owner._dpi_start_thread and runtime_owner._dpi_start_thread.isRunning():
            log("Запуск DPI уже выполняется", "DEBUG")
            return False
    except RuntimeError:
        runtime_owner._dpi_start_thread = None

    if not skip_conflict_prompt and not handle_conflicting_processes_before_start(
        runtime_owner,
        selected_mode,
        launch_method,
    ):
        return False

    runtime_owner._pending_launch_warnings = []
    return True


def _requested_launch_method(runtime_owner, launch_method=None) -> str:
    explicit = normalize_launch_method(launch_method, default="")
    if explicit:
        return explicit
    try:
        snapshot = runtime_owner._runtime_service().snapshot()
        return normalize_launch_method(getattr(snapshot, "launch_method", ""), default="")
    except Exception:
        return ""


def start_dpi_async(
    runtime_owner,
    selected_mode=None,
    launch_method=None,
    *,
    skip_conflict_prompt: bool = False,
    startup_autostart: bool = False,
) -> None:
    """Запускает DPI через общий асинхронный pipeline."""
    if not prepare_start_preflight(
        runtime_owner,
        selected_mode=selected_mode,
        launch_method=launch_method,
        skip_conflict_prompt=skip_conflict_prompt,
    ):
        return

    requested_method = _requested_launch_method(runtime_owner, launch_method)
    if not requested_method:
        fail_start_preparation(runtime_owner, "Не выбран способ запуска DPI")
        return

    mode_name = resolve_mode_name(selected_mode)
    if selected_mode is None or selected_mode == "default":
        mode_name = "Пресет"
    method_name = resolve_method_name(requested_method)

    if isinstance(selected_mode, tuple) and len(selected_mode) == 2:
        strategy_id, strategy_name = selected_mode
        log(f"Обработка встроенной стратегии: {strategy_name} (ID: {strategy_id})", "DEBUG")
    elif isinstance(selected_mode, dict):
        log(f"Обработка стратегии: {mode_name}", "DEBUG")
    elif isinstance(selected_mode, str):
        log(f"Обработка строковой стратегии: {mode_name}", "DEBUG")

    set_runtime_owner_status(runtime_owner, f"🚀 Запуск DPI ({method_name}): {mode_name}")

    if not startup_autostart:
        runtime_owner._runtime_service().set_busy(True, "Запуск Zapret...")

    runtime_owner._begin_runtime_start(requested_method, selected_mode)

    start_worker_thread(
        runtime_owner,
        thread_attr="_dpi_start_thread",
        worker_attr="_dpi_start_worker",
        worker=PresetLaunchStartWorker(
            selected_mode,
            requested_method,
            runtime_feature=runtime_owner._runtime_feature,
            runtime_api=runtime_owner._runtime_api(),
            startup_autostart=bool(startup_autostart),
            prepare_request=True,
        ),
        finished_slot=runtime_owner._on_dpi_start_finished,
        progress_slot=runtime_owner_status_callback(runtime_owner),
        cleanup_log_label="потока запуска",
    )

    log(f"Запуск асинхронного старта DPI: {mode_name} (метод: {method_name})", "INFO")
