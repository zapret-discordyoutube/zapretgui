"""Окно появляется на экране уже нарисованным — при запуске и из трея.

Между show() и первой отрисовкой проходит время: Qt сначала выполняет всё, что
стоит в очереди событий, и только потом рисует окно. Всё это время Windows
показывает окно таким, какое оно есть, — пустым и белым. На тёмной программе
это видно как белая вспышка при запуске (снимки окна на win10: белый кадр
держался от 30 до 100 мс даже после того, как окно стали показывать уже
собранным).

То же при каждом возврате окна из трея: скрытое окно Qt рисует заново целиком,
и Windows сначала выводит его белым (замер на win10: белое через 9–42 мс после
щелчка по значку, готовое — через 31–146 мс; на обычном компьютере в разы
дольше, а после долгого лежания в трее ещё и подкачка памяти).

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
FRAME_WAIT_MAX_MS = 1_500


def wait_for_composition() -> None:
    """Ждёт, пока Windows соберёт уже нарисованный кадр окна для экрана.

    Без этого шторка снималась раньше, чем кадр доходил до экрана: на стенде
    win10 окно после снятия шторки ещё ~20 мс было белым в 4 случаях из 6, с
    ожиданием — ни разу из 5. Ждать приходится не дольше одного кадра экрана.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.dwmapi.DwmFlush()
    except Exception:
        pass


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


class FrameReveal(QObject):
    """Держит окно скрытым от экрана от show() до его отрисовки; годится на каждый показ."""

    def __init__(
        self,
        window,
        *,
        cloak: Callable[[int, bool], bool] = set_window_cloaked,
        wait_composed: Callable[[], None] = wait_for_composition,
        wait_max_ms: int = FRAME_WAIT_MAX_MS,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._cloak = cloak
        self._wait_composed = wait_composed
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
            # проходом цикла событий — когда кадр уже нарисован и отдан системе —
            # и после того, как Windows соберёт его для экрана.
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
        self._wait_composed()
        self._cloak(self._hwnd, False)


def cloak_until_painted(window) -> FrameReveal:
    """Прячет окно от экрана до отрисовки. Вызывается из showEvent при каждом show()."""
    reveal = getattr(window, "_frame_reveal", None)
    if not isinstance(reveal, FrameReveal):
        reveal = FrameReveal(window)
        window._frame_reveal = reveal
    reveal.start()
    return reveal


__all__ = [
    "DWMWA_CLOAK",
    "FRAME_WAIT_MAX_MS",
    "FrameReveal",
    "cloak_until_painted",
    "set_window_cloaked",
    "wait_for_composition",
]
