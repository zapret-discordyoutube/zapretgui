"""The Linux tray: ``QSystemTrayIcon``, which Qt speaks as a StatusNotifierItem (KDE, XFCE, Cinnamon, and GNOME with
the AppIndicator extension). Where no tray exists the factory gives ``NoTrayManager`` instead, so the window is
never hidden without an icon to bring it back.

The menu is small on purpose (show/hide, start/stop, exit); it uses the texts of the Windows tray menu.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ui.launch_control import normalize_launch_phase
from ui.tray_status import build_tray_tooltip, render_status_icon_image, tray_icon_dot_color

try:
    from app.ui_texts import tr as tr_catalog
except Exception:  # pragma: no cover
    def tr_catalog(_key, *, language=None, default=""):
        return default

try:
    from log.log import log
except Exception:  # pragma: no cover
    def log(*_args, **_kwargs):
        return None

ICON_SIZE = 32
STOPPED_NOTIFY_DELAY_MS = 1500


def tray_available() -> bool:
    """Whether the desktop has a tray. Asking Qt before the application object exists crashes the process, so: no."""
    if QApplication.instance() is None:
        return False
    try:
        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:
        return False


class QtTrayManager(QObject):
    def __init__(self, *, window_port, icon_path: str, app_version: str, tray_feature, icon_cls=QSystemTrayIcon, menu_cls=QMenu) -> None:
        super().__init__()
        self.window_port = window_port
        self.icon_path = icon_path
        self.app_version = app_version
        self._feature = tray_feature
        self._phase = ""
        self._launch_method = ""
        self._preset_name = ""
        self._hint_shown = False
        self._base_image = QImage(icon_path) if icon_path else QImage()
        self._icon = icon_cls()
        self._menu = menu_cls()
        self._show_action = self._add_action(self._toggle_window)
        self._menu.addSeparator()
        self._start_stop_action = self._add_action(self._toggle_dpi)
        self._menu.addSeparator()
        self._exit_action = self._add_action(self.exit_only)
        self._exit_stop_action = self._add_action(self.exit_and_stop)
        self._menu.aboutToShow.connect(self._refresh_menu)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._set_icon(None)
        self._refresh_menu()
        self._icon.setToolTip("Zapret")
        self._icon.show()

    # -- menu -------------------------------------------------------------------------------------

    def _add_action(self, handler) -> QAction:
        action = QAction(self._menu)
        action.triggered.connect(lambda _checked=False, fn=handler: fn())
        self._menu.addAction(action)
        return action

    def _language(self) -> str | None:
        try:
            return self.window_port.ui_language()
        except Exception:
            return None

    def _window_visible(self) -> bool:
        try:
            return bool(self.window_port.is_visible())
        except Exception:
            return True

    def _refresh_menu(self) -> None:
        language = self._language()
        self._show_action.setText(
            tr_catalog("tray.menu.hide", language=language, default="Скрыть в трей")
            if self._window_visible()
            else tr_catalog("tray.menu.show", language=language, default="Показать окно")
        )
        running = normalize_launch_phase(self._phase) in {"running", "starting", "autostart_pending"}
        self._start_stop_action.setText(
            tr_catalog("tray.menu.stop", language=language, default="Остановить Zapret")
            if running
            else tr_catalog("tray.menu.start", language=language, default="Запустить Zapret")
        )
        self._exit_action.setText(tr_catalog("tray.menu.exit", language=language, default="Выход"))
        self._exit_stop_action.setText(tr_catalog("tray.menu.exit_stop", language=language, default="Выход и остановить"))

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_window()

    def _toggle_window(self) -> None:
        if self._window_visible():
            self.hide_to_tray(show_hint=False)
        else:
            self.show_window()

    def _toggle_dpi(self) -> None:
        try:
            self._feature.toggle_dpi()
        except Exception as exc:
            log(f"Не удалось переключить DPI из трея: {exc}", "WARNING")

    # -- the manager surface the program uses ---------------------------------------------------------

    def _set_icon(self, color: str | None) -> None:
        image = render_status_icon_image(self._base_image, color, ICON_SIZE)
        self._icon.setIcon(QIcon(QPixmap.fromImage(image)))

    def apply_launch_status(self, *, phase: str, launch_method: str, preset_name: str) -> None:
        self._phase = normalize_launch_phase(phase)
        self._launch_method = str(launch_method or "")
        self._preset_name = str(preset_name or "")
        self._set_icon(tray_icon_dot_color(self._phase))
        self._icon.setToolTip(
            build_tray_tooltip(
                phase=self._phase, launch_method=self._launch_method, preset_name=self._preset_name, language=self._language()
            )
        )
        self._refresh_menu()

    def show_notification(self, title, message, msec=5000) -> None:
        try:
            self._icon.showMessage(str(title), str(message), QSystemTrayIcon.MessageIcon.Information, int(msec))
        except Exception as exc:
            log(f"Уведомление трея не показано: {exc}", "DEBUG")

    def show_window(self) -> None:
        try:
            self.window_port.show()
        except Exception:
            pass

    def toggle_window_visibility(self) -> None:
        self._toggle_window()

    def hide_to_tray(self, show_hint: bool = True) -> bool:
        try:
            self.window_port.persist_geometry()
        except Exception:
            pass
        try:
            self.window_port.hide()
        except Exception as exc:
            log(f"Не удалось скрыть окно в трей: {exc}", "WARNING")
            return False
        if show_hint and not self._hint_shown:
            self._hint_shown = True
            self.show_notification("Zapret продолжает работать", "Свернуто в трей. Кликните по иконке, чтобы открыть окно.")
        return True

    def exit_only(self) -> None:
        self.window_port.request_exit(stop_dpi=False)

    def exit_and_stop(self) -> None:
        self.window_port.request_exit(stop_dpi=True)

    def recreate_icon(self) -> None:
        self._icon.show()

    def prewarm_menu(self) -> None:
        self._refresh_menu()

    def hide_icon(self) -> None:
        self._icon.hide()

    def cleanup(self) -> None:
        self._icon.hide()
        self._icon.setContextMenu(None)
        self._menu.deleteLater()
        self._icon.deleteLater()
