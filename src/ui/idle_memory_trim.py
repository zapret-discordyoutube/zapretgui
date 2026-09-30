"""Освобождение памяти, пока окно программы долго скрыто в трее или свёрнуто.

Пока окно открыто, кэши не трогаем — иначе интерфейс подтормаживал бы.
Когда окно убрано дольше IDLE_TRIM_DELAY_MS, окно никто не видит: можно
собрать мусор, сбросить общий кэш картинок Qt и вернуть Windows страницы
памяти, которые сейчас не нужны. При возврате окна нужное подгрузится само.
"""

from __future__ import annotations

import gc
import sys
import time

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtGui import QPixmapCache

from log.log import log


# Сколько окно должно пробыть скрытым, прежде чем чистить память.
IDLE_TRIM_DELAY_MS = 2 * 60 * 1000
# Пока окно скрыто, фоновая работа (оркестратор, прокси) снова набирает
# страницы — повторяем очистку с таким интервалом.
IDLE_TRIM_REPEAT_MS = 30 * 60 * 1000

_WATCHED_EVENTS = (QEvent.Type.Hide, QEvent.Type.Show, QEvent.Type.WindowStateChange)


def _working_set_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def _release_memory_to_windows() -> None:
    import ctypes
    from ctypes import wintypes

    try:
        # Возвращает системе пустые блоки кучи C-рантайма после крупных освобождений.
        ctypes.cdll.ucrtbase._heapmin()
    except Exception:
        pass

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetProcessWorkingSetSizeEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    )
    kernel32.SetProcessWorkingSetSizeEx.restype = wintypes.BOOL
    # (SIZE_T)-1 в обоих лимитах — «убрать из рабочего набора всё, что можно».
    unlimited = ctypes.c_size_t(-1).value
    kernel32.SetProcessWorkingSetSizeEx(kernel32.GetCurrentProcess(), unlimited, unlimited, 0)


def trim_process_memory() -> None:
    """Собирает мусор, чистит кэш картинок Qt и отдаёт Windows лишние страницы."""
    started = time.perf_counter()
    before = _working_set_mb()
    collected = gc.collect()
    QPixmapCache.clear()
    if sys.platform == "win32":
        try:
            _release_memory_to_windows()
        except Exception as exc:
            log(f"Не удалось вернуть память системе: {exc}", "DEBUG")
    after = _working_set_mb()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if before is not None and after is not None:
        log(
            f"Окно скрыто: память {before:.0f} → {after:.0f} МБ "
            f"(объектов собрано: {collected}, {elapsed_ms:.0f} мс)",
            "INFO",
        )


class IdleMemoryTrimmer(QObject):
    """Следит за окном и чистит память, когда оно долго скрыто или свёрнуто."""

    def __init__(
        self,
        window,
        *,
        delay_ms: int = IDLE_TRIM_DELAY_MS,
        repeat_ms: int = IDLE_TRIM_REPEAT_MS,
        trim=trim_process_memory,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._delay_ms = int(delay_ms)
        self._repeat_ms = int(repeat_ms)
        self._trim = trim
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        window.installEventFilter(self)
        self._sync()

    def _window_is_idle(self) -> bool:
        window = self._window
        return not window.isVisible() or window.isMinimized()

    def _sync(self) -> None:
        if self._window_is_idle():
            if not self._timer.isActive():
                self._timer.start(self._delay_ms)
        else:
            self._timer.stop()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if obj is self._window and event.type() in _WATCHED_EVENTS:
            self._sync()
        return False

    def _on_timeout(self) -> None:
        if not self._window_is_idle():
            return
        try:
            self._trim()
        except Exception as exc:
            log(f"Ошибка очистки памяти скрытого окна: {exc}", "DEBUG")
        self._timer.start(self._repeat_ms)


def install_idle_memory_trim(window) -> IdleMemoryTrimmer:
    return IdleMemoryTrimmer(window)


__all__ = ["IdleMemoryTrimmer", "install_idle_memory_trim", "trim_process_memory"]
