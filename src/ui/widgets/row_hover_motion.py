"""Плавное наведение на строки списка.

Помощник подключается к готовому списку (QListView/QListWidget), строки
которого рисует delegate, и ведёт для каждой строки одно число — уровень
наведения от 0 до 1. Мышь зашла на строку — уровень быстро догоняет 1,
ушла — чуть медленнее возвращается к 0.

Уровень каждый кадр проходит долю оставшегося пути («догоняет цель»), поэтому
отклик виден с первого кадра, а при смене направления на полпути нет рывка.

Сам помощник ничего не рисует: delegate спрашивает ``hover_level`` и по нему
смешивает цвет подложки и проявляет кнопки строки. В покое таймер остановлен
и процессор не тратится. При выключенных «Живых анимациях» ``hover_level``
возвращает None, и delegate рисует обычное мгновенное наведение.
"""

from __future__ import annotations

import math
import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QCursor

from ui.animation_policy import are_live_animations_enabled


# За это время уровень проходит ~63% оставшегося пути; весь переход занимает
# примерно четыре таких отрезка.
FADE_IN_TAU_MS = 22.0
FADE_OUT_TAU_MS = 45.0
_SETTLE_EPSILON = 0.01
_TICK_MS = 8
_MOTION_ATTR = "_zapret_row_hover_motion"


class RowHoverMotion(QObject):
    def __init__(self, view, *, row_filter=None) -> None:
        super().__init__(view)
        self._view = view
        self._row_filter = row_filter
        self._hover_row = -1
        self._levels: dict[int, float] = {}
        self._last_tick = 0.0
        self._model = None

        self._timer = QTimer(self)
        # Обычный таймер Qt на Windows срабатывает неровно, кадры идут рывками.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

        viewport = view.viewport()
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)
        self._bind_model()

    # ---- модель --------------------------------------------------------

    def _bind_model(self) -> None:
        model = self._view.model()
        if model is self._model:
            return
        self._model = model
        self._reset()
        if model is None:
            return
        for signal in (
            model.modelReset,
            model.layoutChanged,
            model.rowsInserted,
            model.rowsRemoved,
            model.rowsMoved,
        ):
            signal.connect(self._reset)

    def _reset(self, *args) -> None:
        _ = args
        self._hover_row = -1
        self._levels.clear()
        self._timer.stop()

    # ---- наведение -----------------------------------------------------

    def _row_at(self, pos) -> int:
        index = self._view.indexAt(pos)
        if not index.isValid():
            return -1
        if self._row_filter is not None:
            try:
                if not self._row_filter(index):
                    return -1
            except Exception:
                return -1
        return index.row()

    def _set_hover_row(self, row: int) -> None:
        if row == self._hover_row:
            return
        previous = self._hover_row
        self._hover_row = row
        if not self._can_animate():
            self._levels.clear()
            self._timer.stop()
            self._repaint_rows({previous, row})
            return
        if previous >= 0:
            self._levels.setdefault(previous, 1.0)
        if row >= 0:
            self._levels.setdefault(row, 0.0)
        if not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()

    def _recheck_under_cursor(self) -> None:
        if not self._alive():
            return
        viewport = self._view.viewport()
        pos = viewport.mapFromGlobal(QCursor.pos())
        if not viewport.rect().contains(pos):
            self._set_hover_row(-1)
            return
        self._set_hover_row(self._row_at(pos))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.HoverMove, QEvent.Type.HoverEnter):
            self._bind_model()
            self._set_hover_row(self._row_at(event.position().toPoint()))
        elif kind in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            self._set_hover_row(-1)
        elif kind == QEvent.Type.Wheel:
            # Под неподвижным курсором строки уезжают при прокрутке.
            QTimer.singleShot(0, self._recheck_under_cursor)
        return False

    # ---- анимация ------------------------------------------------------

    def _can_animate(self) -> bool:
        try:
            window = self._view.window()
            if window is not None and window.isMinimized():
                return False
        except RuntimeError:
            return False
        return are_live_animations_enabled()

    def _alive(self) -> bool:
        return not sip.isdeleted(self._view)

    def _tick(self) -> None:
        if not self._alive():
            self._timer.stop()
            return
        now = time.monotonic()
        dt_ms = max(0.0, (now - self._last_tick) * 1000.0)
        self._last_tick = now

        dirty: set[int] = set()
        moving = False
        for row in list(self._levels):
            target = 1.0 if row == self._hover_row else 0.0
            level = self._levels[row]
            tau = FADE_IN_TAU_MS if target > level else FADE_OUT_TAU_MS
            level += (target - level) * (1.0 - math.exp(-dt_ms / tau))
            if abs(target - level) <= _SETTLE_EPSILON:
                level = target
            else:
                moving = True
            if level <= 0.0:
                del self._levels[row]
            else:
                self._levels[row] = level
            dirty.add(row)

        self._repaint_rows(dirty)
        if not moving:
            self._timer.stop()

    def _repaint_rows(self, rows: set[int]) -> None:
        model = self._view.model()
        if model is None:
            return
        viewport = self._view.viewport()
        for row in rows:
            if 0 <= row < model.rowCount():
                viewport.update(self._view.visualRect(model.index(row, 0)))

    # ---- запросы delegate ----------------------------------------------

    def hover_level(self, index) -> float | None:
        """Насколько проявлено наведение (0..1); None — рисовать как обычно."""
        if not are_live_animations_enabled():
            return None
        row = index.row()
        return self._levels.get(row, 1.0 if row == self._hover_row else 0.0)


def attach_row_hover_motion(view, *, row_filter=None) -> RowHoverMotion:
    """Подключает плавное наведение к списку (один раз на список)."""
    motion = view.__dict__.get(_MOTION_ATTR)
    if motion is None:
        motion = RowHoverMotion(view, row_filter=row_filter)
        view.__dict__[_MOTION_ATTR] = motion
    return motion


def row_hover_motion(view) -> RowHoverMotion | None:
    try:
        return view.__dict__.get(_MOTION_ATTR)
    except Exception:
        return None


__all__ = [
    "RowHoverMotion",
    "attach_row_hover_motion",
    "row_hover_motion",
]
