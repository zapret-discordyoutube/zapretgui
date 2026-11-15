from __future__ import annotations

import sys


class WindowsDesktop:
    """Window and system integration through Windows messages and APIs.

    The helper modules sit next to this file; they are imported inside the methods so the program
    does not pay for tables it never uses (and so a failure in one cannot stop the window).
    """

    def user_idle_ms(self) -> int | None:
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class _LastInputInfo(ctypes.Structure):
                _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))

            info = _LastInputInfo()
            info.cbSize = ctypes.sizeof(_LastInputInfo)
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            kernel32.GetTickCount.restype = wintypes.DWORD
            # Both counters are 32-bit and wrap every ~49 days: take the difference modulo 2**32
            # so a wrap does not give a negative pause.
            return int((int(kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF)
        except Exception:
            return None

    def uses_qt_file_drop(self) -> bool:
        # Explorer does not pass OLE drag-and-drop into an elevated window: only WM_DROPFILES works.
        return False

    def bind_window(self, window) -> bool:
        from platforms.windows.file_drop import enable_windows_file_drop
        from platforms.windows.screen_presence import register_screen_presence_notifications

        enabled = enable_windows_file_drop(window)
        # The same handle receives "session locked" and "display off": animations stop drawing into the void.
        register_screen_presence_notifications(window)
        return enabled

    def dropped_file_paths(self, message) -> list[str] | None:
        from platforms.windows.file_drop import windows_dropped_file_paths

        return windows_dropped_file_paths(message)

    def create_drag_hover_detector(self, window, *, on_hover_start, on_hover_end):
        from platforms.windows.drag_hover_detector import WindowsDragHoverDetector

        return WindowsDragHoverDetector(window, on_hover_start=on_hover_start, on_hover_end=on_hover_end)

    def handle_native_event(self, window, message) -> bool:
        from platforms.windows.file_drop import WM_DROPFILES
        from platforms.windows.native_commands import (
            WM_SYSCOMMAND,
            handle_native_minimize_command,
            is_native_minimize_command,
            is_native_restore_command,
        )
        from platforms.windows.screen_presence import (
            SCREEN_PRESENCE_MESSAGES,
            handle_native_screen_presence,
            native_message_id,
        )

        # Every Windows message of the window comes through here: parse the structure once,
        # then look only at our own numbers.
        message_id = native_message_id(message)
        if message_id == WM_DROPFILES:
            from ui.window_preset_file_drop import handle_native_preset_file_drop

            return bool(handle_native_preset_file_drop(window, message))
        if message_id == WM_SYSCOMMAND:
            if is_native_restore_command(message):
                window._note_return_intent()
            elif is_native_minimize_command(message):
                window._note_away_intent()
            return bool(
                handle_native_minimize_command(
                    window,
                    message,
                    minimize_to_tray_enabled=window._minimize_to_tray_enabled,
                )
            )
        if message_id in SCREEN_PRESENCE_MESSAGES:
            handle_native_screen_presence(message)
        return False

    def cloak_until_painted(self, window):
        from platforms.windows.first_frame import cloak_until_painted

        return cloak_until_painted(window)

    def install_theme_watcher(self, app, window):
        from platforms.windows.system_theme import install_windows_system_theme_watcher

        return install_windows_system_theme_watcher(app, window)
