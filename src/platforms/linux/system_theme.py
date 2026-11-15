"""Light/dark theme of the Linux desktop.

Qt (6.5 and newer) reads the colour scheme from the XDG desktop portal (``org.freedesktop.appearance color-scheme``,
set by GNOME, KDE, XFCE with xdg-desktop-portal) and tells about changes; this module only listens and applies the
program's theme when the person chose "follow the system". The decision itself is the same code as on Windows.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable

from PyQt6.QtCore import QObject, QProcess, Qt, QTimer

from utils.qt_signal_links import SignalLinks

try:
    from log.log import log
except Exception:  # pragma: no cover
    def log(*_args, **_kwargs):
        return None


PORTAL_DESTINATION = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_READ = [
    "gdbus", "call", "--session", "--dest", PORTAL_DESTINATION, "--object-path", PORTAL_PATH,
    "--method", "org.freedesktop.portal.Settings.Read", "org.freedesktop.appearance", "color-scheme",
]
_PORTAL_MONITOR = ["gdbus", "monitor", "--session", "--dest", PORTAL_DESTINATION]


def parse_portal_scheme(text: str) -> str | None:
    """The answer of ``Settings.Read`` looks like ``(<<uint32 1>>,)``: 1 = dark, 2 = light, 0 = no preference."""
    match = re.search(r"uint32\s+(\d+)", text or "")
    if not match:
        return None
    return {"1": "dark", "2": "light"}.get(match.group(1))


def read_portal_theme(run=subprocess.run) -> str | None:
    """Asks the desktop portal directly. Needed because the Qt that ships with the program has no platform theme for
    the portal (that is a plugin of the desktop), so ``QStyleHints.colorScheme()`` stays "unknown" on KDE and GNOME."""
    if not shutil.which("gdbus"):
        return None
    try:
        done = run(_PORTAL_READ, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_portal_scheme(done.stdout) if done.returncode == 0 else None


def read_system_theme(app) -> str | None:
    """"dark", "light", or None when the desktop does not say (no portal, no gdbus)."""
    try:
        scheme = app.styleHints().colorScheme()
    except Exception:
        scheme = None
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return read_portal_theme()


class LinuxSystemThemeWatcher(QObject):
    def __init__(self, *, app, window, apply_theme: Callable[..., bool] | None = None, debounce_ms: int = 80) -> None:
        super().__init__()
        self._app = app
        self._window = window
        self._apply_theme = apply_theme
        self._links = SignalLinks()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(debounce_ms)))
        self._timer.timeout.connect(self.refresh_now)
        self._monitor: QProcess | None = None

    def install(self) -> bool:
        watching = False
        try:
            self._links.connect(self._app.styleHints().colorSchemeChanged, self._on_scheme_changed)
            watching = True
        except Exception as exc:  # Qt older than 6.5: no signal
            log(f"Слежение за темой через Qt недоступно: {exc}", "DEBUG")
        if self._qt_knows_the_scheme():
            return watching
        # Qt does not hear the portal: listen to its signals ourselves (no polling: the process only speaks on a change).
        return self._start_portal_monitor() or watching

    def _qt_knows_the_scheme(self) -> bool:
        try:
            return self._app.styleHints().colorScheme() in (Qt.ColorScheme.Dark, Qt.ColorScheme.Light)
        except Exception:
            return False

    def _start_portal_monitor(self) -> bool:
        if not shutil.which("gdbus"):
            return False
        process = QProcess(self)
        process.setProgram(_PORTAL_MONITOR[0])
        process.setArguments(_PORTAL_MONITOR[1:])
        process.readyReadStandardOutput.connect(lambda: self._on_monitor_output(bytes(process.readAllStandardOutput())))
        process.start()
        self._monitor = process
        return True

    def _on_monitor_output(self, data: bytes) -> None:
        if b"color-scheme" in data:
            self._timer.start()

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
        if self._monitor is not None:
            self._monitor.kill()
            self._monitor = None
        self._links.disconnect_all()


def install_system_theme_watcher(app, window) -> LinuxSystemThemeWatcher | None:
    if app is None:
        return None
    watcher = LinuxSystemThemeWatcher(app=app, window=window)
    if not watcher.install():
        return None
    watcher.refresh_now()
    return watcher
