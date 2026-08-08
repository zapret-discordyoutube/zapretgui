"""Определяет перенос файла из Проводника над повышенным окном.

Windows не передаёт окну с правами администратора события наведения при
переносе, поэтому здесь используется опрос косвенных признаков: курсор над
нашим окном, зажатая левая кнопка, ввод не захвачен нашим потоком и в этой
зажатой кнопке уже была замечена картинка переноса Проводника.

Важно: проверять окно-картинку «здесь и сейчас» нельзя. Над повышенным окном
OLE-цель запрещена (UIPI), эффект переноса становится «нельзя», и Проводник
уничтожает окно SysDragImage ровно в тот момент, когда курсор оказывается над
нами; при возврате на обычное окно картинка создаётся заново. Поэтому факт
переноса фиксируется «липкой сессией»: картинка видна где-то в системе при
зажатой кнопке — до отпускания кнопки считаем, что тащат файл.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer

from log.log import log


GA_ROOT = 2
VK_LBUTTON = 0x01
KEY_PRESSED_FLAG = 0x8000
SHELL_DRAG_IMAGE_CLASS = "SysDragImage"
DEFAULT_POLL_INTERVAL_MS = 120


@dataclass(frozen=True)
class ShellDragSignals:
    """Сырые признаки одного опроса; при ошибке или не-Windows — все False."""

    button_down: bool = False
    own_capture: bool = False
    over_window: bool = False
    drag_image_present: bool = False


def _windows_api(name: str, provided=None):
    if provided is not None:
        return provided
    return getattr(getattr(ctypes, "windll"), name)


def read_shell_drag_signals(
    window,
    *,
    user32=None,
    platform: str | None = None,
) -> ShellDragSignals:
    """Считывает признаки переноса одним снимком."""
    if (platform or sys.platform) != "win32":
        return ShellDragSignals()

    try:
        user32 = _windows_api("user32", user32)

        if not (int(user32.GetAsyncKeyState(VK_LBUTTON)) & KEY_PRESSED_FLAG):
            return ShellDragSignals()

        # Захват мыши нашим потоком означает наш собственный перенос или
        # зажатие внутри окна — подсказка про внешний файл не нужна.
        own_capture = bool(int(user32.GetCapture() or 0))

        drag_image_present = bool(
            int(user32.FindWindowW(SHELL_DRAG_IMAGE_CLASS, None) or 0)
        )

        over_window = False
        if window.isVisible() and not window.isMinimized():
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                under_cursor = int(user32.WindowFromPoint(point) or 0)
                if under_cursor:
                    top_level = int(
                        user32.GetAncestor(
                            wintypes.HWND(under_cursor),
                            wintypes.UINT(GA_ROOT),
                        )
                        or 0
                    )
                    over_window = top_level == int(window.winId())

        return ShellDragSignals(
            button_down=True,
            own_capture=own_capture,
            over_window=over_window,
            drag_image_present=drag_image_present,
        )
    except Exception as exc:
        log(f"Не удалось определить перенос файла над окном: {exc}", "DEBUG")
        return ShellDragSignals()


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
        self._shell_drag_session = False
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
        self._shell_drag_session = False
        self._apply_hover_state(False)

    def poll(self) -> None:
        signals = read_shell_drag_signals(
            self._window,
            user32=self._user32,
            platform=self._platform,
        )
        if not signals.button_down:
            self._shell_drag_session = False
        elif signals.drag_image_present:
            self._shell_drag_session = True
        self._apply_hover_state(
            signals.button_down
            and self._shell_drag_session
            and not signals.own_capture
            and signals.over_window
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
    "ShellDragSignals",
    "WindowsDragHoverDetector",
    "read_shell_drag_signals",
]
