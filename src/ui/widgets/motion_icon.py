"""Небольшой значок с короткими жестами: подпрыгнуть и поблёскивать.

Значок стоит на месте, пока ничего не происходит. Жест длится доли секунды
и запускается только по событию (изменилось значение) или по редкому
одиночному таймеру (поблёскивание), поэтому процессор в покое не тратится.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled


BOUNCE_DURATION_MS = 560
TWINKLE_DURATION_MS = 900
DEFAULT_TWINKLE_INTERVAL_MS = 9000

GESTURE_NONE = ""
GESTURE_BOUNCE = "bounce"
GESTURE_TWINKLE = "twinkle"


class MotionIcon(QWidget):
    """Замена QLabel с картинкой: тот же setPixmap/pixmap, плюс жесты."""

    def __init__(self, parent=None, *, size: int = 24) -> None:
        super().__init__(parent)
        side = max(12, int(size))
        self._side = side
        # Запас по краям, чтобы прыжок и покачивание не обрезались.
        self.setFixedSize(side + 4, side + 8)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._pixmap = QPixmap()
        self._glow: QColor | None = None
        self._gesture = GESTURE_NONE
        self._t = 0.0
        self._twinkle_interval_ms = 0

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start.
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)

        self._twinkle_timer = QTimer(self)
        self._twinkle_timer.setSingleShot(True)
        self._twinkle_timer.timeout.connect(self._on_twinkle_timer)

    # ---- API как у QLabel ----------------------------------------------

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802 (как у QLabel)
        self._pixmap = QPixmap(pixmap)
        self.update()

    def pixmap(self) -> QPixmap:
        return QPixmap(self._pixmap)

    # ---- жесты ---------------------------------------------------------

    def set_glow(self, color: str | None) -> None:
        """Мягкое неподвижное сияние позади значка (None — без сияния)."""
        glow = QColor(color) if color else None
        if glow is not None and not glow.isValid():
            glow = None
        if glow == self._glow:
            return
        self._glow = glow
        self.update()

    def set_idle_twinkle(self, interval_ms: int | None) -> None:
        """Изредка поблёскивать; 0 или None — не поблёскивать."""
        self._twinkle_interval_ms = max(0, int(interval_ms or 0))
        self._schedule_twinkle()

    def bounce(self) -> None:
        self._play(GESTURE_BOUNCE, BOUNCE_DURATION_MS)

    def twinkle(self) -> None:
        self._play(GESTURE_TWINKLE, TWINKLE_DURATION_MS)

    def gesture(self) -> str:
        return self._gesture

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _play(self, gesture: str, duration_ms: int) -> None:
        if not self._can_animate():
            return
        self._anim.stop()
        self._gesture = gesture
        self._t = 0.0
        self._anim.setDuration(duration_ms)
        self._anim.start()

    def _schedule_twinkle(self) -> None:
        self._twinkle_timer.stop()
        if self._twinkle_interval_ms > 0 and self._can_animate():
            self._twinkle_timer.start(self._twinkle_interval_ms)

    def _on_twinkle_timer(self) -> None:
        if not self._anim.state() == QVariantAnimation.State.Running:
            self.twinkle()
        self._schedule_twinkle()

    def _on_value(self, value) -> None:
        try:
            self._t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_finished(self) -> None:
        self._gesture = GESTURE_NONE
        self._t = 0.0
        self.update()

    # ---- жизненный цикл ------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_twinkle()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._twinkle_timer.stop()
        self._anim.stop()
        self._on_finished()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._twinkle_timer.stop()
                self._anim.stop()
                self._on_finished()
            else:
                self._schedule_twinkle()

    # ---- отрисовка -----------------------------------------------------

    def _transform(self) -> tuple[float, float, float, float]:
        """Возвращает (сдвиг по y, масштаб, поворот, яркость блика)."""
        t = self._t
        if self._gesture == GESTURE_BOUNCE:
            # Прыжок вверх с затухающим отскоком и лёгким сжатием при приземлении.
            lift = -3.5 * math.sin(math.pi * min(1.0, t / 0.45)) if t < 0.45 else 0.0
            settle = 1.2 * math.sin(math.pi * (t - 0.45) / 0.3) if 0.45 <= t < 0.75 else 0.0
            squash = 1.0 + 0.08 * math.sin(math.pi * min(1.0, t / 0.45))
            return lift + settle, squash, 0.0, 0.0
        if self._gesture == GESTURE_TWINKLE:
            # Звезда чуть подрастает, покачивается и по ней проходит блик.
            wave = math.sin(math.pi * t)
            tilt = 20.0 * math.sin(2.0 * math.pi * t) * (1.0 - t)
            return 0.0, 1.0 + 0.24 * wave, tilt, wave
        return 0.0, 1.0, 0.0, 0.0

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        center = QPointF(self.width() / 2, self.height() / 2)
        side = float(self._side)
        dy, scale, angle, sparkle = self._transform()

        if self._glow is not None:
            radius = side / 2
            gradient = QRadialGradient(center, radius)
            inner = QColor(self._glow)
            inner.setAlphaF(0.45 + 0.45 * sparkle)
            outer = QColor(self._glow)
            outer.setAlphaF(0.0)
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(1.0, outer)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(center, radius, radius)

        if not self._pixmap.isNull():
            dpr = self._pixmap.devicePixelRatio() or 1.0
            w = self._pixmap.width() / dpr
            h = self._pixmap.height() / dpr
            painter.save()
            painter.translate(center.x(), center.y() + dy)
            painter.rotate(angle)
            painter.scale(scale, scale)
            painter.drawPixmap(QRectF(-w / 2, -h / 2, w, h), self._pixmap, QRectF(self._pixmap.rect()))
            painter.restore()

        if sparkle > 0.05:
            # Маленькая четырёхлучевая искра у правого верхнего кончика.
            spark = QColor(255, 255, 255)
            spark.setAlphaF(min(1.0, sparkle))
            r = side * 0.28 * sparkle
            cx, cy = center.x() + side * 0.3, center.y() - side * 0.3
            path = QPainterPath()
            path.moveTo(cx, cy - r)
            path.quadTo(cx, cy, cx + r, cy)
            path.quadTo(cx, cy, cx, cy + r)
            path.quadTo(cx, cy, cx - r, cy)
            path.quadTo(cx, cy, cx, cy - r)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(spark)
            painter.drawPath(path)
        painter.end()


__all__ = ["MotionIcon"]
