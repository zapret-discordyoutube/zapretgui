from __future__ import annotations


class WindowsTray:
    """Creates the native Shell_NotifyIcon tray manager (imported on first use: it prepares many ctypes tables)."""

    def create_manager(self, *, window_port, icon_path: str, app_version: str, tray_feature):
        from platforms.windows.tray import SystemTrayManager

        return SystemTrayManager(window_port, icon_path, app_version, tray_feature=tray_feature)
