"""Определяет перенос файла из Проводника над повышенным окном.

Windows не передаёт окну с правами администратора события наведения при
переносе, поэтому здесь используется опрос косвенных признаков: курсор над
нашим окном, зажатая левая кнопка, ввод захвачен чужим процессом и в системе
существует окно-картинка перетаскивания Проводника.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PyQt6.QtCore import QObject, QTimer

from log.log import log


GA_ROOT = 2
VK_LBUTTON = 0x01
KEY_PRESSED_FLAG = 0x8000
SHELL_DRAG_IMAGE_CLASS = "SysDragImage"
DEFAULT_POLL_INTERVAL_MS = 120


def _windows_api(name: str, provided=None):
    if provided is not None:
        return provided
    return getattr(getattr(ctypes, "windll"), name)


def shell_file_drag_over_window(
    window,
    *,
    user32=None,
    platform: str | None = None,
) -> bool:
    """True, когда Проводник тащит что-то над нашим верхним окном."""
    if (platform or sys.platform) != "win32":
        return False

    try:
        user32 = _windows_api("user32", user32)
        if not window.isVisible() or window.isMinimized():
            return False

        if not (int(user32.GetAsyncKeyState(VK_LBUTTON)) & KEY_PRESSED_FLAG):
            return False
        # Захват мыши нашим потоком означает наш собственный перенос или
        # зажатие внутри окна — подсказка про внешний файл не нужна.
        if int(user32.GetCapture() or 0):
            return False

        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return False
        under_cursor = int(user32.WindowFromPoint(point) or 0)
        if not under_cursor:
            return False
        top_level = int(
            user32.GetAncestor(
                wintypes.HWND(under_cursor),
                wintypes.UINT(GA_ROOT),
            )
            or 0
        )
        if top_level != int(window.winId()):
            return False

        return bool(int(user32.FindWindowW(SHELL_DRAG_IMAGE_CLASS, None) or 0))
    except Exception as exc:
        log(f"Не удалось определить перенос файла над окном: {exc}", "DEBUG")
        return False


class WindowsDragHoverDetector(QObject):
    """Опрашивает признаки переноса и сообщает о начале и конце наведения."""

    def __init__(
        self,
        window,
        *,
        on_hover_start: Callable[[], object],
        on_hover_end: Callable[[], object],
        interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
        user32=None,
        platform: str | None = None,
    ) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self._window = window
        self._on_hover_start = on_hover_start
        self._on_hover_end = on_hover_end
        self._user32 = user32
        self._platform = platform
        self._hovering = False
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval_ms))
        self._timer.timeout.connect(self.poll)

    def start(self) -> bool:
        if (self._platform or sys.platform) != "win32":
            return False
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._apply_hover_state(False)

    def poll(self) -> None:
        self._apply_hover_state(
            shell_file_drag_over_window(
                self._window,
                user32=self._user32,
                platform=self._platform,
            )
        )

    def _apply_hover_state(self, hovering: bool) -> None:
        if hovering == self._hovering:
            return
        self._hovering = hovering
        callback = self._on_hover_start if hovering else self._on_hover_end
        try:
            callback()
        except Exception as exc:
            log(f"Не удалось обновить подсказку переноса файла: {exc}", "ERROR")


__all__ = [
    "DEFAULT_POLL_INTERVAL_MS",
    "GA_ROOT",
    "KEY_PRESSED_FLAG",
    "SHELL_DRAG_IMAGE_CLASS",
    "VK_LBUTTON",
    "WindowsDragHoverDetector",
    "shell_file_drag_over_window",
]
