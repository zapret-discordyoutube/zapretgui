from __future__ import annotations

from PyQt6.QtCore import pyqtSignal

from main.runtime_state import log_startup_metric as emit_startup_metric
from main.window_actions import WindowActionsMixin
from main.window_lifecycle import WindowLifecycleMixin
from main.window_startup import WindowStartupMixin
from main.window_state_sync import WindowStateSyncMixin
from ui.fluent_app_window import ZapretFluentWindow
from ui.window_ui_facade import MainWindowUI

class LupiDPIApp(
    WindowStartupMixin,
    WindowLifecycleMixin,
    WindowActionsMixin,
    WindowStateSyncMixin,
    ZapretFluentWindow,
    MainWindowUI,
):
    """Главное окно приложения — FluentWindow + навигация."""

    deferred_init_requested = pyqtSignal()
    continue_startup_requested = pyqtSignal()
    finalize_ui_bootstrap_requested = pyqtSignal()
    startup_interactive_ready = pyqtSignal(str)
    startup_post_init_ready = pyqtSignal(str)

    def log_startup_metric(self, marker: str, details: str = "") -> None:
        emit_startup_metric(marker, details)

__all__ = ["LupiDPIApp"]
