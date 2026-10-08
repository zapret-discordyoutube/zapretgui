"""Движение в списке стратегий: наведение и смена выбранной стратегии.

Правил два, и оба нарочно простые:
- плитка под мышью плавно подсвечивается и так же плавно гаснет;
- выбранная плитка плавно загорается цветом акцента, прежняя — гаснет.

Ничего не летает по списку, не крутится и не блестит: на сетке плиток в
несколько столбцов такие эффекты выглядят рывками. Уровни привязаны к ключу
строки, а не к её номеру, поэтому перестройка списка (после применения
стратегии, при поиске) анимацию не сбивает.

Сам помощник ничего не рисует: ``delegate`` спрашивает у него два числа от 0
до 1 — ``hover_level`` и ``active_level``. Таймер идёт, только пока хоть одно
из них меняется.
"""

from __future__ import annotations

import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtGui import QCursor

from ui.animation_policy import are_live_animations_enabled

HOVER_IN_MS = 90
HOVER_OUT_MS = 180
ACTIVE_IN_MS = 200
ACTIVE_OUT_MS = 240
# Сколько плитка, на которую щёлкнули, считается выбранной «авансом», пока
# программа применяет стратегию и список ещё не знает о новой выбранной.
OPTIMISTIC_MS = 2500
_TICK_MS = 16


def _smooth(level: float) -> float:
    """Мягкий разгон и торможение вместо равномерного перехода."""
    return level * level * (3.0 - 2.0 * level)


class TileMotion(QObject):
    def __init__(self, view, *, key_at, rect_of_key) -> None:
        """key_at(точка) — ключ строки под мышью ("" — нет); rect_of_key(ключ) — где она нарисована."""
        super().__init__(view)
        self._view = view
        self._key_at = key_at
        self._rect_of_key = rect_of_key
        self._hover_key = ""
        self._active_key = ""
        self._optimistic_until = 0.0
        self._hover: dict[str, float] = {}
        self._active: dict[str, float] = {}
        self._last_tick = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        viewport = view.viewport()
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)

    # ---- что сообщает список -----------------------------------------

    def set_active(self, key: str, *, optimistic: bool = False, animate: bool = True) -> None:
        """Выбрана другая стратегия.

        optimistic — человек только что щёлкнул: плитка загорается сразу, не
        дожидаясь, пока стратегия применится и список это подтвердит.
        """
        key = str(key or "")
        now = time.monotonic()
        if optimistic:
            self._optimistic_until = now + OPTIMISTIC_MS / 1000.0
        elif key != self._active_key and now < self._optimistic_until:
            # Список ещё показывает прежнюю выбранную: щелчок важнее.
            return
        else:
            self._optimistic_until = 0.0
        if key == self._active_key:
            return
        previous, self._active_key = self._active_key, key
        if not animate or not self._can_animate():
            self._active.clear()
            self._repaint((previous, key))
            return
        if previous:
            self._active.setdefault(previous, 1.0)
        if key:
            self._active.setdefault(key, 0.0)
        self._start()

    def cancel_optimistic(self) -> None:
        self._optimistic_until = 0.0

    def active_key(self) -> str:
        return self._active_key

    # ---- что спрашивает delegate -------------------------------------

    def hover_level(self, key: str) -> float:
        return _smooth(self._hover.get(key, 1.0 if key == self._hover_key else 0.0))

    def active_level(self, key: str) -> float:
        return _smooth(self._active.get(key, 1.0 if key == self._active_key else 0.0))

    def is_running(self) -> bool:
        return self._timer.isActive()

    # ---- наведение ---------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.HoverMove, QEvent.Type.HoverEnter):
            self._set_hover(self._key_at(event.position().toPoint()))
        elif kind in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            self._set_hover("")
        elif kind == QEvent.Type.Wheel:
            # Под неподвижной мышью строки уезжают при прокрутке.
            QTimer.singleShot(0, self._recheck_under_cursor)
        return False

    def _recheck_under_cursor(self) -> None:
        if sip.isdeleted(self._view):
            return
        viewport = self._view.viewport()
        pos = viewport.mapFromGlobal(QCursor.pos())
        self._set_hover(self._key_at(pos) if viewport.rect().contains(pos) else "")

    def _set_hover(self, key: str) -> None:
        key = str(key or "")
        if key == self._hover_key:
            return
        previous, self._hover_key = self._hover_key, key
        if not self._can_animate():
            self._hover.clear()
            self._repaint((previous, key))
            return
        if previous:
            self._hover.setdefault(previous, 1.0)
        if key:
            self._hover.setdefault(key, 0.0)
        self._start()

    # ---- ход времени -------------------------------------------------

    def _can_animate(self) -> bool:
        try:
            window = self._view.window()
            if not self._view.isVisible() or (window is not None and window.isMinimized()):
                return False
        except RuntimeError:
            return False
        return are_live_animations_enabled()

    def _start(self) -> None:
        if not self._timer.isActive():
            self._last_tick = time.monotonic()
            self._timer.start()

    @staticmethod
    def _step(levels: dict[str, float], target_key: str, dt_ms: float, in_ms: int, out_ms: int) -> set[str]:
        touched = set(levels)
        for key in list(levels):
            level = levels[key]
            if key == target_key:
                level = min(1.0, level + dt_ms / in_ms)
                done = level >= 1.0
            else:
                level = max(0.0, level - dt_ms / out_ms)
                done = level <= 0.0
            if done:
                del levels[key]
            else:
                levels[key] = level
        return touched

    def _tick(self) -> None:
        if sip.isdeleted(self._view):
            self._timer.stop()
            return
        now = time.monotonic()
        # Длинный кадр (программа была занята) не должен «проглотить» переход целиком.
        dt_ms = min(48.0, max(0.0, (now - self._last_tick) * 1000.0))
        self._last_tick = now
        touched = self._step(self._hover, self._hover_key, dt_ms, HOVER_IN_MS, HOVER_OUT_MS)
        touched |= self._step(self._active, self._active_key, dt_ms, ACTIVE_IN_MS, ACTIVE_OUT_MS)
        self._repaint(touched)
        if not self._hover and not self._active:
            self._timer.stop()

    def _repaint(self, keys) -> None:
        viewport = self._view.viewport()
        for key in keys:
            if not key:
                continue
            rect = self._rect_of_key(key)
            if rect is not None and rect.isValid():
                viewport.update(rect)


__all__ = ["TileMotion"]
