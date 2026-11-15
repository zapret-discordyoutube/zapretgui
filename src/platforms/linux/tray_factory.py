from __future__ import annotations

from platforms.no_tray import NoTrayManager

GNOME_HINT = (
    "В этой среде рабочего стола нет области уведомлений. В GNOME включите расширение «AppIndicator and "
    "KStatusNotifierItem Support»; пока значка нет, окно не прячется в трей."
)


class LinuxTray:
    """The StatusNotifierItem tray where the desktop has a tray, a quiet stand-in where it has not."""

    def create_manager(self, *, window_port, icon_path: str, app_version: str, tray_feature):
        from platforms.linux import qt_tray

        if qt_tray.tray_available():
            return qt_tray.QtTrayManager(
                window_port=window_port, icon_path=icon_path, app_version=app_version, tray_feature=tray_feature
            )
        try:
            from log.log import log

            log(f"Системный трей недоступен. {GNOME_HINT}", "WARNING")
        except Exception:
            pass
        return NoTrayManager(
            window_port=window_port, icon_path=icon_path, app_version=app_version, tray_feature=tray_feature
        )
