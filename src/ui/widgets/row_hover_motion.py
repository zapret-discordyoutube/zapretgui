"""Живое наведение на строки списка: мягкая подсветка, блик и покачивание значка.

Помощник подключается к готовому списку (QListView/QListWidget), строки
которого рисует delegate. Когда мышь заходит на строку:
- полупрозрачная подсветка наведения плавно проявляется (а при уходе
  плавно гаснет) вместо мгновенного переключения;
- по строке один раз проходит едва заметный полупрозрачный блик;
- значок строки слегка покачивается и затухает.

Сам помощник ничего не рисует: delegate спрашивает у него уровень подсветки,
положение блика и угол значка (см. ``hover_level``, ``sheen_progress``,
``icon_angle``). В покое таймер остановлен и процессор не тратится.
При выключенных «Живых анимациях» ``hover_level`` возвращает None, и delegate
рисует обычное мгновенное наведение.
"""

from __future__ import annotations

import math
import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRectF, QTimer
from PyQt6.QtGui import QCursor, QPainter

from ui.animation_policy import are_live_animations_enabled


FADE_IN_MS = 150
FADE_OUT_MS = 250
SHEEN_DURATION_MS = 520
WOBBLE_DURATION_MS = 560
WOBBLE_AMPLITUDE_DEG = 9.0
WOBBLE_SWINGS = 2.0
_TICK_MS = 16
_MOTION_ATTR = "_zapret_row_hover_motion"


class RowHoverMotion(QObject):
    def __init__(self, view, *, row_filter=None) -> None:
        super().__init__(view)
        self._view = view
        self._row_filter = row_filter
        self._hover_row = -1
        self._levels: dict[int, float] = {}
        self._started_at: dict[int, float] = {}
        self._last_tick = 0.0
        self._model = None

        self._timer = QTimer(self)
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
        self._started_at.clear()
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
            self._started_at.clear()
            return
        if previous >= 0:
            self._levels.setdefault(previous, 1.0)
        if row >= 0:
            self._levels.setdefault(row, 0.0)
            self._started_at[row] = time.monotonic()
        self._start_timer()

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

    def _start_timer(self) -> None:
        if not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()

    def _tick(self) -> None:
        if not self._alive():
            self._timer.stop()
            return
        now = time.monotonic()
        dt_ms = max(0.0, (now - self._last_tick) * 1000.0)
        self._last_tick = now

        dirty: set[int] = set()
        for row in list(self._levels):
            target = 1.0 if row == self._hover_row else 0.0
            level = self._levels[row]
            if level < target:
                level = min(target, level + dt_ms / FADE_IN_MS)
            elif level > target:
                level = max(target, level - dt_ms / FADE_OUT_MS)
            if level <= 0.0 and target == 0.0:
                del self._levels[row]
            else:
                self._levels[row] = level
            dirty.add(row)

        longest = max(SHEEN_DURATION_MS, WOBBLE_DURATION_MS) / 1000.0
        for row, started in list(self._started_at.items()):
            dirty.add(row)
            if now - started > longest:
                del self._started_at[row]

        self._repaint_rows(dirty)
        settled = not self._started_at and all(
            level == (1.0 if row == self._hover_row else 0.0) for row, level in self._levels.items()
        )
        if settled:
            self._timer.stop()

    def _repaint_rows(self, rows: set[int]) -> None:
        model = self._view.model()
        if model is None:
            return
        viewport = self._view.viewport()
        for row in rows:
            if 0 <= row < model.rowCount():
                viewport.update(self._view.visualRect(model.index(row, 0)))

    def _elapsed(self, index) -> float | None:
        started = self._started_at.get(index.row())
        if started is None:
            return None
        return time.monotonic() - started

    # ---- запросы delegate ----------------------------------------------

    def hover_level(self, index) -> float | None:
        """Насколько проявлена подсветка наведения (0..1); None — рисовать как обычно."""
        if not are_live_animations_enabled():
            return None
        return self._levels.get(index.row(), 1.0 if index.row() == self._hover_row else 0.0)

    def sheen_progress(self, index) -> float | None:
        """Положение блика вдоль строки (0..1) или None, если блика нет."""
        elapsed = self._elapsed(index)
        if elapsed is None:
            return None
        t = elapsed * 1000.0 / SHEEN_DURATION_MS
        return t if 0.0 <= t < 1.0 else None

    def icon_angle(self, index) -> float:
        elapsed = self._elapsed(index)
        if elapsed is None:
            return 0.0
        t = elapsed * 1000.0 / WOBBLE_DURATION_MS
        if t <= 0.0 or t >= 1.0:
            return 0.0
        return WOBBLE_AMPLITUDE_DEG * math.sin(2.0 * math.pi * WOBBLE_SWINGS * t) * (1.0 - t) ** 1.3


def paint_rotated(painter: QPainter, rect, angle: float, draw) -> None:
    """Рисует draw() с поворотом на angle градусов вокруг центра rect."""
    if abs(angle) < 0.01:
        draw()
        return
    center = QRectF(rect).center()
    painter.save()
    try:
        painter.translate(center)
        painter.rotate(angle)
        painter.translate(-center)
        draw()
    finally:
        painter.restore()


def attach_row_hover_motion(view, *, row_filter=None) -> RowHoverMotion:
    """Подключает живое наведение к списку (один раз на список)."""
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
    "paint_rotated",
    "row_hover_motion",
]
