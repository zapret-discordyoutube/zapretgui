"""Light/dark theme of the Linux desktop.

Qt (6.5 and newer) reads the colour scheme from the XDG desktop portal (``org.freedesktop.appearance color-scheme``,
set by GNOME, KDE, XFCE with xdg-desktop-portal) and tells about changes; this module only listens and applies the
program's theme when the person chose "follow the system". The decision itself is the same code as on Windows.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, QTimer

try:
    from log.log import log
except Exception:  # pragma: no cover
    def log(*_args, **_kwargs):
        return None


def read_system_theme(app) -> str | None:
    """"dark", "light", or None when the desktop does not say (no portal, old Qt)."""
    try:
        scheme = app.styleHints().colorScheme()
    except Exception:
        return None
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return None


class LinuxSystemThemeWatcher(QObject):
    def __init__(self, *, app, window, apply_theme: Callable[..., bool] | None = None, debounce_ms: int = 80) -> None:
        super().__init__()
        self._app = app
        self._window = window
        self._apply_theme = apply_theme
        self._connected = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(debounce_ms)))
        self._timer.timeout.connect(self.refresh_now)

    def install(self) -> bool:
        try:
            self._app.styleHints().colorSchemeChanged.connect(self._on_scheme_changed)
        except Exception as exc:  # Qt older than 6.5: no signal, the theme is read once at start
            log(f"Слежение за темой рабочего стола недоступно: {exc}", "DEBUG")
            return False
        self._connected = True
        return True

    def _on_scheme_changed(self, *_args) -> None:
        self._timer.start()

    def refresh_now(self) -> bool:
        apply = self._apply_theme
        if apply is None:
            from platforms.windows.system_theme import apply_windows_system_theme_if_auto

            apply = apply_windows_system_theme_if_auto
        return bool(apply(self._window, system_theme_reader=lambda: read_system_theme(self._app)))

    def cleanup(self) -> None:
        self._timer.stop()
        if self._connected:
            try:
                self._app.styleHints().colorSchemeChanged.disconnect(self._on_scheme_changed)
            except Exception:
                pass
            self._connected = False


def install_system_theme_watcher(app, window) -> LinuxSystemThemeWatcher | None:
    if app is None:
        return None
    watcher = LinuxSystemThemeWatcher(app=app, window=window)
    if not watcher.install():
        return None
    watcher.refresh_now()
    return watcher
