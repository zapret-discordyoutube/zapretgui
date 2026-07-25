from __future__ import annotations

from PyQt6.QtCore import QTimer

from log.log import log

from .discord_restart_flow import maybe_restart_discord_after_runtime_apply
from .status_feedback import set_runtime_owner_status


def _start_worker_result(runtime_owner) -> tuple[int | None, list[str]]:
    """Забирает уже готовый результат worker без обращения к runner/WinAPI."""
    worker = getattr(runtime_owner, "_dpi_start_worker", None)
    pid = getattr(worker, "started_pid", None)
    warnings = list(getattr(worker, "warnings", []) or [])
    return (pid if isinstance(pid, int) else None), warnings


def show_launch_error_top(runtime_owner, message: str) -> None:
    """Показывает человеко-понятную ошибку запуска через верхний InfoBar."""
    bridge = runtime_owner._runtime_ui_bridge()
    if bridge is not None:
        bridge.show_launch_error(message)


def show_launch_warning_top(runtime_owner, message: str) -> None:
    bridge = runtime_owner._runtime_ui_bridge()
    if bridge is not None:
        bridge.show_launch_warning(message)


def on_dpi_start_finished(runtime_owner, success, error_message):
    """Обрабатывает завершение асинхронного запуска DPI."""
    completed_restart_generation = int(runtime_owner._restart_active_start_generation or 0)
    try:
        runtime_owner._runtime_service().set_busy(False)

        if success:
            pid, warnings = _start_worker_result(runtime_owner)
            runtime_owner._pending_launch_warnings = warnings
            runtime_owner._mark_runtime_running(pid=pid)
            if completed_restart_generation:
                runtime_owner._restart_completed_generation = max(
                    runtime_owner._restart_completed_generation,
                    completed_restart_generation,
                )
                runtime_owner._restart_active_start_generation = 0

            log("DPI запущен асинхронно", "INFO")
            set_runtime_owner_status(runtime_owner, "✅ DPI успешно запущен")
            runtime_owner._runtime_feature.flags.mark_intentional_start()
            maybe_restart_discord_after_runtime_apply(runtime_owner, skip_first_start=True)

            pending_warnings = list(runtime_owner._pending_launch_warnings or [])
            runtime_owner._pending_launch_warnings = []
            for warning_text in pending_warnings:
                log(f"Launch warning: {warning_text}", "WARNING")
                QTimer.singleShot(150, lambda text=warning_text: show_launch_warning_top(runtime_owner, text))
        else:
            if completed_restart_generation:
                runtime_owner._restart_completed_generation = max(
                    runtime_owner._restart_completed_generation,
                    completed_restart_generation,
                )
                runtime_owner._restart_active_start_generation = 0
            log(f"Ошибка асинхронного запуска DPI: {error_message}", "❌ ERROR")
            set_runtime_owner_status(runtime_owner, f"❌ Ошибка запуска: {error_message}")
            show_launch_error_top(runtime_owner, error_message)
            runtime_owner._mark_runtime_failed(error_message)

    except Exception as e:
        log(f"Ошибка при обработке результата запуска DPI: {e}", "❌ ERROR")
        runtime_owner._runtime_service().set_busy(False)
        set_runtime_owner_status(runtime_owner, f"Ошибка: {e}")
    finally:
        if runtime_owner._restart_request_generation > runtime_owner._restart_completed_generation:
            QTimer.singleShot(0, runtime_owner._process_pending_restart_request)
        if runtime_owner._presets_switch_requested_generation > runtime_owner._presets_switch_completed_generation:
            QTimer.singleShot(0, runtime_owner._process_pending_presets_switch)


def on_dpi_stop_finished(runtime_owner, success, error_message):
    """Обрабатывает завершение асинхронной остановки DPI."""
    restart_generation_after_stop = int(runtime_owner._restart_pending_stop_generation or 0)
    try:
        runtime_owner._runtime_service().set_busy(False)

        if success:
            log("DPI остановлен асинхронно", "INFO")
            if error_message:
                set_runtime_owner_status(runtime_owner, f"✅ {error_message}")
            else:
                set_runtime_owner_status(runtime_owner, "✅ DPI успешно остановлен")
            runtime_owner._mark_runtime_stopped()
            if restart_generation_after_stop >= int(runtime_owner._restart_force_stop_generation or 0):
                runtime_owner._restart_force_stop_generation = 0

            if restart_generation_after_stop > runtime_owner._restart_completed_generation:
                runtime_owner._restart_pending_stop_generation = 0
                runtime_owner._restart_active_start_generation = max(
                    restart_generation_after_stop,
                    runtime_owner._restart_request_generation,
                )
                runtime_owner.start_dpi_async()
                return
        else:
            log(f"Ошибка асинхронной остановки DPI: {error_message}", "❌ ERROR")
            set_runtime_owner_status(runtime_owner, f"❌ Ошибка остановки: {error_message}")

            # Stop worker возвращает False только если после его собственных
            # фоновых проверок процесс всё ещё жив.
            runtime_owner._mark_runtime_running()
            if restart_generation_after_stop >= int(runtime_owner._restart_force_stop_generation or 0):
                runtime_owner._restart_force_stop_generation = 0

            runtime_owner._restart_pending_stop_generation = 0

    except Exception as e:
        log(f"Ошибка при обработке результата остановки DPI: {e}", "❌ ERROR")
        set_runtime_owner_status(runtime_owner, f"Ошибка: {e}")
    finally:
        if runtime_owner._presets_switch_requested_generation > runtime_owner._presets_switch_completed_generation:
            QTimer.singleShot(0, runtime_owner._process_pending_presets_switch)


def on_stop_and_exit_finished(runtime_owner):
    """Завершает приложение после остановки DPI."""
    set_runtime_owner_status(runtime_owner, "Завершение...")
    from PyQt6.QtWidgets import QApplication

    try:
        QApplication.closeAllWindows()
    except Exception:
        pass

    QApplication.quit()


def cleanup_threads(runtime_owner):
    """Очищает все потоки при закрытии приложения."""
    try:
        for thread_attr, log_message in (
            ("_dpi_start_thread", "Останавливаем поток запуска DPI..."),
            ("_dpi_stop_thread", "Останавливаем поток остановки DPI..."),
            ("_stop_exit_thread", "Останавливаем поток stop-and-exit..."),
            ("_discord_restart_thread", "Останавливаем поток перезапуска Discord..."),
        ):
            thread = getattr(runtime_owner, thread_attr, None)
            if thread is None:
                continue
            if thread.isRunning():
                log(log_message, "DEBUG")
                thread.quit()
            else:
                setattr(runtime_owner, thread_attr, None)

    except Exception as e:
        log(f"Ошибка при очистке потоков DPI runtime: {e}", "❌ ERROR")
