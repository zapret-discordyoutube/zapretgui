from __future__ import annotations


class LinuxDesktop:
    """Window integration on Linux. Qt handles file drops and window state itself; the idle counter,
    the XDG portal theme and the frameless-window differences are added in the Linux integration phase."""

    def user_idle_ms(self) -> int | None:
        return None

    def uses_qt_file_drop(self) -> bool:
        return True

    def bind_window(self, window) -> bool:
        return False

    def dropped_file_paths(self, message) -> list[str] | None:
        return None

    def create_drag_hover_detector(self, window, *, on_hover_start, on_hover_end):
        return None  # Qt delivers drag-enter events to a normal window

    def handle_native_event(self, window, message) -> bool:
        return False

    def cloak_until_painted(self, window):
        return None

    def install_theme_watcher(self, app, window):
        from platforms.linux.system_theme import install_system_theme_watcher

        return install_system_theme_watcher(app, window)
