"""Глобус, который будто поворачивается: плавно сменяет материки и чуть парит.

Раз в несколько секунд значок мягко перетекает в следующий вид Земли
(Америка → Европа → Африка → Азия), а между сменами едва заметно
поднимается и опускается. Анимация идёт, только пока глобус виден, окно
не свёрнуто и включены «Живые анимации»; иначе показан один материк.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QEvent, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled


GLOBE_ICONS = ("fa5s.globe-americas", "fa5s.globe-europe", "fa5s.globe-africa", "fa5s.globe-asia")
TURN_PERIOD_MS = 3200
TURN_DURATION_MS = 900
FLOAT_PERIOD_MS = 3200
FLOAT_PX = 2.5
_FLOAT_TICK_MS = 33


class TurningGlobe(QWidget):
    def __init__(self, parent=None, *, size: int = 48, color: str = "#60cdff") -> None:
        super().__init__(parent)
        self._side = max(16, int(size))
        self._color = str(color)
        self._index = 0
        self._blend = 0.0
        self._float_phase = 0.0
        # Глобус перерисовывается ~30 раз в секунду — значки держим готовыми.
        self._icons: dict[tuple[str, str], object] = {}
        self.setFixedSize(self._side + 8, self._side + 8)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._turn = QVariantAnimation(self)
        self._turn.setStartValue(0.0)
        self._turn.setEndValue(1.0)
        self._turn.setDuration(TURN_DURATION_MS)
        self._turn.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._turn.valueChanged.connect(self._on_blend)
        self._turn.finished.connect(self._on_turned)

        self._pause = QTimer(self)
        self._pause.setSingleShot(True)
        self._pause.timeout.connect(self._begin_turn)

        self._float = QTimer(self)
        self._float.setInterval(_FLOAT_TICK_MS)
        self._float.timeout.connect(self._on_float_tick)

    def set_color(self, color: str) -> None:
        self._color = str(color)
        self._icons.clear()
        self.update()

    def _icon(self, name: str):
        key = (name, self._color)
        icon = self._icons.get(key)
        if icon is None:
            from ui.theme import get_themed_qta_icon

            icon = get_themed_qta_icon(name, color=self._color)
            self._icons[key] = icon
        return icon

    def current_icon(self) -> str:
        return GLOBE_ICONS[self._index]

    def is_animating(self) -> bool:
        return self._pause.isActive() or self._float.isActive() or self._turn.state() == QVariantAnimation.State.Running

    # ---- цикл ----------------------------------------------------------

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _start(self) -> None:
        if not self._can_animate():
            return
        if not self._pause.isActive() and self._turn.state() != QVariantAnimation.State.Running:
            self._pause.start(TURN_PERIOD_MS - TURN_DURATION_MS)
        if not self._float.isActive():
            self._float.start()

    def _stop(self) -> None:
        self._pause.stop()
        self._turn.stop()
        self._float.stop()
        self._blend = 0.0
        self._float_phase = 0.0
        self.update()

    def _begin_turn(self) -> None:
        if not self._can_animate():
            self._stop()
            return
        self._turn.start()

    def _on_blend(self, value) -> None:
        self._blend = float(value)
        self.update()

    def _on_turned(self) -> None:
        self._index = (self._index + 1) % len(GLOBE_ICONS)
        self._blend = 0.0
        self.update()
        if self._can_animate():
            self._pause.start(TURN_PERIOD_MS - TURN_DURATION_MS)

    def _on_float_tick(self) -> None:
        self._float_phase = (self._float_phase + _FLOAT_TICK_MS / FLOAT_PERIOD_MS) % 1.0
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._stop()
            else:
                self._start()

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        dy = -FLOAT_PX * math.sin(2.0 * math.pi * self._float_phase)
        left = (self.width() - self._side) / 2.0
        top = (self.height() - self._side) / 2.0 + dy
        rect = QRectF(left, top, self._side, self._side).toRect()

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            current = self._icon(GLOBE_ICONS[self._index])
            if self._blend <= 0.0:
                current.paint(painter, rect)
                return
            following = self._icon(GLOBE_ICONS[(self._index + 1) % len(GLOBE_ICONS)])
            painter.setOpacity(1.0 - self._blend)
            current.paint(painter, rect)
            painter.setOpacity(self._blend)
            following.paint(painter, rect)
        finally:
            painter.end()


__all__ = ["GLOBE_ICONS", "TurningGlobe"]
