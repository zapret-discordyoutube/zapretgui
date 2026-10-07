"""Окно появляется на экране уже нарисованным.

Между show() и первой отрисовкой проходит время: Qt сначала выполняет всё, что
стоит в очереди событий, и только потом рисует окно. Всё это время Windows
показывает окно таким, какое оно есть, — пустым и белым. На тёмной программе
это видно как белая вспышка при запуске (снимки окна на win10: белый кадр
держался от 30 до 100 мс даже после того, как окно стали показывать уже
собранным).

У Windows для этого есть штатное средство — скрытие окна от экрана (cloak):
окно считается показанным, получает события и рисуется, но на экран не
выводится. Перед первым показом окно скрывается, после первой отрисовки —
открывается, и пользователь видит сразу готовый кадр.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, QTimer


# DwmSetWindowAttribute: «скрыть окно от экрана, продолжая его рисовать».
DWMWA_CLOAK = 13
# Дольше этого окно скрытым не остаётся, даже если отрисовка так и не пришла:
# белое окно лучше невидимой программы.
FIRST_FRAME_WAIT_MAX_MS = 1_500


def set_window_cloaked(hwnd: int, cloaked: bool) -> bool:
    """Скрывает окно от экрана или открывает его. True — Windows приняла запрос."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        value = ctypes.c_int(1 if cloaked else 0)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(int(hwnd)),
            DWMWA_CLOAK,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return int(result) == 0
    except Exception:
        return False


class FirstFrameReveal(QObject):
    """Держит окно скрытым от экрана от первого show() до первой отрисовки."""

    def __init__(
        self,
        window,
        *,
        cloak: Callable[[int, bool], bool] = set_window_cloaked,
        wait_max_ms: int = FIRST_FRAME_WAIT_MAX_MS,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._cloak = cloak
        self._hwnd = 0
        self._cloaked = False
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(max(0, int(wait_max_ms)))
        self._deadline.timeout.connect(self.reveal)

    def start(self) -> bool:
        """Вызывается из showEvent: окно ещё не выведено на экран."""
        if self._cloaked:
            return True
        try:
            hwnd = int(self._window.winId())
        except Exception:
            return False
        if not self._cloak(hwnd, True):
            return False
        self._hwnd = hwnd
        self._cloaked = True
        self._window.installEventFilter(self)
        self._deadline.start()
        return True

    def is_cloaked(self) -> bool:
        return self._cloaked

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API)
        if self._cloaked and watched is self._window and event.type() == QEvent.Type.Paint:
            watched.removeEventFilter(self)
            # Событие приходит до самой отрисовки. Открываем окно следующим
            # проходом цикла событий — когда кадр уже нарисован и отдан системе.
            QTimer.singleShot(0, self.reveal)
        return False

    def reveal(self) -> None:
        if not self._cloaked:
            return
        self._cloaked = False
        self._deadline.stop()
        try:
            self._window.removeEventFilter(self)
        except Exception:
            pass
        self._cloak(self._hwnd, False)


def begin_first_frame_reveal(window) -> FirstFrameReveal:
    """Прячет окно от экрана до первой отрисовки. Вызывается из showEvent."""
    reveal = FirstFrameReveal(window)
    reveal.start()
    return reveal


__all__ = [
    "DWMWA_CLOAK",
    "FIRST_FRAME_WAIT_MAX_MS",
    "FirstFrameReveal",
    "begin_first_frame_reveal",
    "set_window_cloaked",
]
