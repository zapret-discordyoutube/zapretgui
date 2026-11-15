"""The Linux theme watcher: reads Qt's portal-backed colour scheme and applies the program theme only in "follow the system" mode."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from platforms.linux import system_theme
from platforms.linux.system_theme import LinuxSystemThemeWatcher, read_system_theme


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeHints(QObject):
    colorSchemeChanged = pyqtSignal(object)

    def __init__(self, scheme) -> None:
        super().__init__()
        self.scheme = scheme

    def colorScheme(self):  # noqa: N802
        return self.scheme


def _app(scheme):
    return SimpleNamespace(styleHints=lambda hints=FakeHints(scheme): hints)


def test_the_scheme_maps_to_light_dark_or_unknown(qapp) -> None:
    assert read_system_theme(_app(Qt.ColorScheme.Dark)) == "dark"
    assert read_system_theme(_app(Qt.ColorScheme.Light)) == "light"
    assert read_system_theme(_app(Qt.ColorScheme.Unknown)) is None


def test_an_app_that_cannot_tell_is_unknown_not_an_error() -> None:
    assert read_system_theme(SimpleNamespace(styleHints=lambda: (_ for _ in ()).throw(RuntimeError("no portal")))) is None


def test_a_change_of_the_scheme_applies_the_theme_once_after_the_debounce(qapp) -> None:
    app = _app(Qt.ColorScheme.Dark)
    apply = MagicMock(return_value=True)
    watcher = LinuxSystemThemeWatcher(app=app, window="window", apply_theme=apply, debounce_ms=0)
    assert watcher.install() is True
    hints = app.styleHints()
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)  # two quick changes count as one
    from PyQt6.QtTest import QTest

    QTest.qWait(60)
    assert apply.call_count == 1
    args, kwargs = apply.call_args
    assert args == ("window",) and kwargs["system_theme_reader"]() == "dark"
    watcher.cleanup()
    hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
    QTest.qWait(40)
    assert apply.call_count == 1  # disconnected


def test_the_program_theme_follows_only_in_the_system_mode() -> None:
    from platforms.windows.system_theme import apply_windows_system_theme_if_auto

    set_theme = MagicMock()
    theme_enum = SimpleNamespace(DARK="DARK", LIGHT="LIGHT")
    common = dict(
        system_theme_reader=lambda: "light",
        set_theme_func=set_theme,
        theme_enum=theme_enum,
        is_dark_theme_func=lambda: True,
        background_applier=lambda _w: None,
        refresh_flusher=lambda _w: 0,
    )
    assert apply_windows_system_theme_if_auto("w", display_mode_loader=lambda: "dark", **common) is False
    set_theme.assert_not_called()
    with patch("ui.theme.invalidate_theme_tokens_cache"):
        assert apply_windows_system_theme_if_auto("w", display_mode_loader=lambda: "system", **common) is True
    set_theme.assert_called_once_with("LIGHT")


def test_the_desktop_installs_the_watcher_and_applies_the_theme_at_once(qapp) -> None:
    from platforms.linux import PLATFORM

    app = _app(Qt.ColorScheme.Light)
    with patch.object(LinuxSystemThemeWatcher, "refresh_now", return_value=True) as refresh:
        watcher = PLATFORM.desktop.install_theme_watcher(app, "window")
    assert isinstance(watcher, LinuxSystemThemeWatcher)
    refresh.assert_called_once_with()
    watcher.cleanup()


def test_without_a_color_scheme_signal_no_watcher_is_returned(qapp) -> None:
    broken = SimpleNamespace(styleHints=lambda: SimpleNamespace())
    assert system_theme.install_system_theme_watcher(broken, "window") is None
    assert system_theme.install_system_theme_watcher(None, "window") is None
