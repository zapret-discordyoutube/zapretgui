from __future__ import annotations

import time as _time

from log.log import log
from main.runtime_state import log_startup_metric as emit_startup_metric


def connect_window_startup_signals(window, *, continue_startup) -> None:
    window.deferred_init_requested.connect(window._deferred_init, window._queued_connection())
    window.continue_startup_requested.connect(continue_startup, window._queued_connection())
    window.finalize_ui_bootstrap_requested.connect(window._finalize_ui_bootstrap, window._queued_connection())


def show_initial_window_if_needed(window) -> None:
    """Первый показ окна. Вызывается после сборки интерфейса (_deferred_init)."""
    if window.start_in_tray or window.isVisible():
        return

    t_show = _time.perf_counter()
    window.show()
    emit_startup_metric(
        "StartupWindowInitShowCall",
        f"{(_time.perf_counter() - t_show) * 1000:.0f}ms",
    )
    log("Основное окно показано (FluentWindow, интерфейс собран)", "DEBUG")


def start_window_deferred_init(window) -> None:
    window.deferred_init_requested.emit()


__all__ = [
    "connect_window_startup_signals",
    "show_initial_window_if_needed",
    "start_window_deferred_init",
]
