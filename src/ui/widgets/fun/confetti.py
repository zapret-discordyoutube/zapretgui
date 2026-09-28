"""Короткий салют из конфетти поверх виджета.

Слой ложится поверх указанного виджета, пропускает клики насквозь, около
1,3 с рисует разлетающиеся бумажки и сам себя удаляет. Если «живые
анимации» выключены или виджет не виден, салюта просто нет.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled

CONFETTI_DURATION_MS = 1300
CONFETTI_PIECES = 46
_DEFAULT_COLORS = ("#60cdff", "#6ccb5f", "#ffb900", "#ff6b9d", "#b18cff", "#ff8c42")


class _Piece:
    __slots__ = ("x", "y", "vx", "vy", "spin", "angle", "w", "h", "color")

    def __init__(self, rng: random.Random, origin: QPointF, colors) -> None:
        direction = rng.uniform(-math.pi * 0.9, -math.pi * 0.1)
        speed = rng.uniform(180.0, 420.0)
        self.x = origin.x()
        self.y = origin.y()
        self.vx = math.cos(direction) * speed
        self.vy = math.sin(direction) * speed
        self.spin = rng.uniform(-720.0, 720.0)
        self.angle = rng.uniform(0.0, 360.0)
        self.w = rng.uniform(5.0, 9.0)
        self.h = rng.uniform(3.0, 5.0)
        self.color = QColor(rng.choice(colors))


class ConfettiLayer(QWidget):
    def __init__(self, target: QWidget, *, origin: QPointF | None = None, colors=_DEFAULT_COLORS, seed=None) -> None:
        super().__init__(target)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(target.rect())
        rng = random.Random(seed)
        start = origin if origin is not None else QPointF(target.width() / 2, target.height() * 0.45)
        self._pieces = [_Piece(rng, start, colors) for _ in range(CONFETTI_PIECES)]
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(CONFETTI_DURATION_MS)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._done)

    def start(self) -> None:
        self.raise_()
        self.show()
        self._anim.start()

    def _on_value(self, value) -> None:
        self._t = float(value)
        self.update()

    def _done(self) -> None:
        self.hide()
        self.deleteLater()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        seconds = self._t * CONFETTI_DURATION_MS / 1000.0
        gravity = 620.0
        fade = 1.0 if self._t < 0.6 else max(0.0, 1.0 - (self._t - 0.6) / 0.4)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for piece in self._pieces:
            x = piece.x + piece.vx * seconds
            y = piece.y + piece.vy * seconds + gravity * seconds * seconds / 2
            color = QColor(piece.color)
            color.setAlphaF(fade)
            painter.save()
            painter.translate(x, y)
            painter.rotate(piece.angle + piece.spin * seconds)
            # «Бумажка» переворачивается: ширина то сужается, то растёт.
            flip = abs(math.cos((piece.angle + piece.spin * seconds) * math.pi / 180.0 * 2))
            painter.setBrush(color)
            painter.drawRect(QRectF(-piece.w / 2, -piece.h * flip / 2, piece.w, max(1.0, piece.h * flip)))
            painter.restore()
        painter.end()


def burst_confetti(target: QWidget, *, origin: QPointF | None = None, seed=None) -> ConfettiLayer | None:
    """Салют поверх ``target``. Возвращает слой (или None, если салют не нужен)."""
    if target is None or not target.isVisible() or not are_live_animations_enabled():
        return None
    window = target.window()
    if window is not None and window.isMinimized():
        return None
    layer = ConfettiLayer(target, origin=origin, seed=seed)
    layer.start()
    return layer


__all__ = ["CONFETTI_DURATION_MS", "ConfettiLayer", "burst_confetti"]
