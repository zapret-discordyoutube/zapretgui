from __future__ import annotations

from platforms.no_tray import NoTrayManager


class LinuxTray:
    """No tray icon yet: the StatusNotifierItem backend arrives in the Linux integration phase."""

    def create_manager(self, *, window_port, icon_path: str, app_version: str, tray_feature):
        return NoTrayManager(
            window_port=window_port, icon_path=icon_path, app_version=app_version, tray_feature=tray_feature
        )
