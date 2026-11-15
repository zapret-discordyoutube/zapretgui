"""A tray manager for desktops without a notification-area icon (or before a backend exists).

It keeps the rest of the program simple: every call is safe, the window stays visible, and the person is
never left with a hidden window and no icon to click.
"""

from __future__ import annotations


class NoTrayManager:
    def __init__(self, *, window_port=None, icon_path: str = "", app_version: str = "", tray_feature=None) -> None:
        self.window_port = window_port
        self.icon_path = icon_path
        self.app_version = app_version

    def apply_launch_status(self, *, phase: str, launch_method: str, preset_name: str) -> None:
        pass

    def show_notification(self, title, message, msec=5000) -> None:
        """Without a tray icon a notification can still reach the person through the desktop's notification service
        (``notify-send``, present on every common Linux desktop); where that is missing too, nothing is shown."""
        import shutil
        import subprocess
        import sys

        if not sys.platform.startswith("linux"):
            return
        program = shutil.which("notify-send")
        if not program:
            return
        try:
            subprocess.Popen(
                [program, "--app-name=Zapret", f"--expire-time={int(msec)}", str(title), str(message)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def show_window(self) -> None:
        port = self.window_port
        if port is not None:
            try:
                port.show()
            except Exception:
                pass

    def toggle_window_visibility(self) -> None:
        self.show_window()

    def hide_to_tray(self, show_hint: bool = True) -> bool:
        return False  # no icon to restore the window from: do not hide it

    def exit_only(self) -> None:
        if self.window_port is not None:
            self.window_port.request_exit(stop_dpi=False)

    def exit_and_stop(self) -> None:
        if self.window_port is not None:
            self.window_port.request_exit(stop_dpi=True)

    def recreate_icon(self) -> None:
        pass

    def prewarm_menu(self) -> None:
        pass

    def hide_icon(self) -> None:
        pass

    def cleanup(self) -> None:
        pass
