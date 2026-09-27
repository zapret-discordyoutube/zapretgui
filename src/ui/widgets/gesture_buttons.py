"""Кнопки, у которых значок делает короткий жест при нажатии.

Это обычные PushButton/PrimaryPushButton из qfluentwidgets: меняется только
отрисовка значка на время жеста (доли секунды). В покое кнопка ничем не
отличается от стандартной и процессор не тратит.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QVariantAnimation
from qfluentwidgets import PrimaryPushButton, PushButton

from ui.animation_policy import are_animations_enabled


ICON_GESTURE_NONE = ""
# Треугольник «Запустить» подаётся вперёд и возвращается.
ICON_GESTURE_NUDGE = "nudge"
# Квадрат «Стоп» сжимается и пружинит обратно.
ICON_GESTURE_SQUEEZE = "squeeze"
# Значок питания делает оборот.
ICON_GESTURE_SPIN = "spin"

_GESTURE_DURATIONS_MS = {
    ICON_GESTURE_NUDGE: 420,
    ICON_GESTURE_SQUEEZE: 460,
    ICON_GESTURE_SPIN: 620,
}


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


class IconGestureMixin:
    """Добавляет кнопке жест значка по клику. Ставится перед классом кнопки."""

    _icon_gesture = ICON_GESTURE_NONE
    _icon_gesture_t = 0.0
    _icon_gesture_anim: QVariantAnimation | None = None

    def set_icon_gesture(self, gesture: str) -> None:
        self._icon_gesture = str(gesture or ICON_GESTURE_NONE)
        if self._icon_gesture and not getattr(self, "_icon_gesture_connected", False):
            self._icon_gesture_connected = True
            self.clicked.connect(self.play_icon_gesture)

    def icon_gesture(self) -> str:
        return self._icon_gesture

    def is_icon_gesture_running(self) -> bool:
        anim = self._icon_gesture_anim
        return anim is not None and anim.state() != QVariantAnimation.State.Stopped

    def play_icon_gesture(self) -> None:
        duration = _GESTURE_DURATIONS_MS.get(self._icon_gesture)
        if not duration or not self.isVisible() or not are_animations_enabled():
            return
        anim = self._icon_gesture_anim
        if anim is None:
            # QVariantAnimation, а не QPropertyAnimation: при выключенных
            # анимациях общий fallback подменяет QPropertyAnimation.start.
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.valueChanged.connect(self._on_icon_gesture_value)
            anim.finished.connect(self._on_icon_gesture_finished)
            self._icon_gesture_anim = anim
        anim.stop()
        anim.setDuration(duration)
        anim.start()

    def _on_icon_gesture_value(self, value) -> None:
        try:
            self._icon_gesture_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_icon_gesture_finished(self) -> None:
        self._icon_gesture_t = 0.0
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._icon_gesture_anim is not None:
            self._icon_gesture_anim.stop()
        self._icon_gesture_t = 0.0
        super().hideEvent(event)

    def _icon_gesture_transform(self) -> tuple[float, float, float]:
        """Возвращает (сдвиг по x, масштаб, поворот) для текущего кадра."""
        t = self._icon_gesture_t
        if t <= 0.0 or not self.is_icon_gesture_running():
            return 0.0, 1.0, 0.0
        gesture = self._icon_gesture
        if gesture == ICON_GESTURE_NUDGE:
            return 4.0 * math.sin(math.pi * t) * (1.0 - 0.3 * t), 1.0 + 0.1 * math.sin(math.pi * t), 0.0
        if gesture == ICON_GESTURE_SQUEEZE:
            # Быстро сжимается, затем пружинит с маленьким перелётом.
            squeeze = 0.42 * math.sin(math.pi * min(1.0, t / 0.4)) if t < 0.4 else 0.0
            spring = -0.08 * math.sin(math.pi * (t - 0.4) / 0.6) if t >= 0.4 else 0.0
            return 0.0, 1.0 - squeeze - spring, 0.0
        if gesture == ICON_GESTURE_SPIN:
            return 0.0, 1.0, 360.0 * _ease_out_cubic(t)
        return 0.0, 1.0, 0.0

    def _drawIcon(self, icon, painter, rect, *args, **kwargs):  # noqa: N802 (qfluentwidgets API)
        dx, scale, angle = self._icon_gesture_transform()
        if dx == 0.0 and scale == 1.0 and angle == 0.0:
            return super()._drawIcon(icon, painter, rect, *args, **kwargs)
        center = rect.center()
        painter.save()
        painter.translate(center.x() + dx, center.y())
        painter.rotate(angle)
        painter.scale(scale, scale)
        painter.translate(-center.x(), -center.y())
        try:
            return super()._drawIcon(icon, painter, rect, *args, **kwargs)
        finally:
            painter.restore()


class GesturePushButton(IconGestureMixin, PushButton):
    """PushButton, значок которого делает жест при нажатии."""


class GesturePrimaryPushButton(IconGestureMixin, PrimaryPushButton):
    """PrimaryPushButton, значок которого делает жест при нажатии."""


__all__ = [
    "GesturePrimaryPushButton",
    "GesturePushButton",
    "ICON_GESTURE_NUDGE",
    "ICON_GESTURE_SPIN",
    "ICON_GESTURE_SQUEEZE",
    "IconGestureMixin",
]
