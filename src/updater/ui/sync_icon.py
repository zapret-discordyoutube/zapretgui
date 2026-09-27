"""Значок проверки обновлений на странице «Серверы».

Рисуется вручную: мягкий круг цвета акцента и две стрелки по кругу. Пока идёт
проверка, стрелки вращаются и слегка «дышат» длиной; после проверки они не
замирают рывком, а плавно доворачиваются до ровного положения.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled


ICON_MODE_IDLE = "idle"
ICON_MODE_CHECKING = "checking"
ICON_MODE_ERROR = "error"

# Один цикл анимации: два оборота и один вдох/выдох длины стрелок.
CHECKING_CYCLE_MS = 2200
CHECKING_CYCLE_DEGREES = 720.0
SETTLE_DURATION_MS = 650
ARC_SPAN_DEGREES = 128.0
ARC_BREATH_DEGREES = 26.0


class UpdateSyncIcon(QWidget):
    """Круглый значок с двумя стрелками обновления."""

    def __init__(self, parent=None, *, size: int = 40) -> None:
        super().__init__(parent)
        side = max(24, int(size))
        self.setFixedSize(side, side)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._accent = QColor("#5caee8")
        self._error = QColor("#f87171")
        self._is_light = False
        self._error_glyph: QPixmap | None = None
        self._mode = ICON_MODE_IDLE
        self._angle = 0.0
        self._span = ARC_SPAN_DEGREES
        self._running_requested = False

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start, а
        # бесконечный цикл с нулевой длительностью крутил бы процессор.
        self._spin = QVariantAnimation(self)
        self._spin.setStartValue(0.0)
        self._spin.setEndValue(1.0)
        self._spin.setDuration(CHECKING_CYCLE_MS)
        self._spin.setLoopCount(-1)
        self._spin.valueChanged.connect(self._on_spin_value)

        self._settle = QVariantAnimation(self)
        self._settle.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._settle.setDuration(SETTLE_DURATION_MS)
        self._settle.valueChanged.connect(self._on_settle_value)
        self._settle.finished.connect(self._on_settle_finished)
        self._settle_from_span = ARC_SPAN_DEGREES
        self._settle_from_angle = 0.0
        self._settle_to_angle = 0.0

        # Запуск откладываем до показа: проверка при старте программы может
        # начаться, пока страница ещё собирается в фоне и скрыта.
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._start_spin_after_show)

    # ---- публичное API -------------------------------------------------

    def set_colors(self, *, accent: str, error: str, is_light: bool) -> None:
        accent_color = QColor(accent)
        if accent_color.isValid():
            self._accent = accent_color
        error_color = QColor(error)
        if error_color.isValid():
            self._error = error_color
        self._is_light = bool(is_light)
        self.update()

    def set_error_glyph(self, pixmap: QPixmap | None) -> None:
        self._error_glyph = pixmap
        self.update()

    def mode(self) -> str:
        return self._mode

    def angle(self) -> float:
        return self._angle

    def is_spinning(self) -> bool:
        return self._spin.state() == QVariantAnimation.State.Running

    def set_mode(self, mode: str) -> None:
        mode = str(mode or ICON_MODE_IDLE)
        if mode == ICON_MODE_CHECKING:
            self._mode = ICON_MODE_CHECKING
            self._running_requested = True
            self._settle.stop()
            self._schedule_spin_start()
            self.update()
            return

        self._running_requested = False
        self._start_timer.stop()
        was_spinning = self.is_spinning()
        self._spin.stop()
        if mode == ICON_MODE_ERROR:
            self._mode = ICON_MODE_ERROR
            self._settle.stop()
            self._reset_pose()
        else:
            self._mode = ICON_MODE_IDLE
            if was_spinning and self.isVisible():
                self._start_settle()
            else:
                self._settle.stop()
                self._reset_pose()
        self.update()

    # ---- анимация ------------------------------------------------------

    def _reset_pose(self) -> None:
        self._angle = 0.0
        self._span = ARC_SPAN_DEGREES

    def _schedule_spin_start(self) -> None:
        if self._running_requested:
            self._start_timer.start(0)

    def _start_spin_after_show(self) -> None:
        if not self._running_requested or not self.isVisible():
            return
        if not are_live_animations_enabled():
            return
        if not self.is_spinning():
            self._spin.start()

    def _on_spin_value(self, value) -> None:
        try:
            phase = float(value)
        except (TypeError, ValueError):
            return
        self._angle = (phase * CHECKING_CYCLE_DEGREES) % 360.0
        self._span = ARC_SPAN_DEGREES + ARC_BREATH_DEGREES * math.sin(phase * 2.0 * math.pi)
        self.update()

    def _start_settle(self) -> None:
        # Стрелки симметричны, поэтому ровное положение повторяется каждые
        # 180°. Доворачиваем до следующего такого положения с запасом.
        self._settle_from_angle = self._angle
        self._settle_from_span = self._span
        self._settle_to_angle = (math.floor(self._angle / 180.0) + 2) * 180.0
        self._settle.stop()
        self._settle.setStartValue(0.0)
        self._settle.setEndValue(1.0)
        self._settle.start()

    def _on_settle_value(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        self._angle = self._settle_from_angle + (self._settle_to_angle - self._settle_from_angle) * t
        self._span = self._settle_from_span + (ARC_SPAN_DEGREES - self._settle_from_span) * t
        self.update()

    def _on_settle_finished(self) -> None:
        self._reset_pose()
        self.update()

    # ---- события -------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_spin_start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._start_timer.stop()
        self._spin.stop()
        self._settle.stop()
        self._reset_pose()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        window = self.window()
        if window is not None and window.isMinimized():
            self._spin.stop()
        else:
            self._schedule_spin_start()

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        side = float(min(self.width(), self.height()))
        center = QPointF(self.width() / 2, self.height() / 2)

        base = self._error if self._mode == ICON_MODE_ERROR else self._accent
        self._paint_backdrop(painter, center, side, base)

        if self._mode == ICON_MODE_ERROR and self._error_glyph is not None:
            glyph = self._error_glyph
            dpr = glyph.devicePixelRatio() or 1.0
            gw, gh = glyph.width() / dpr, glyph.height() / dpr
            painter.drawPixmap(QRectF(center.x() - gw / 2, center.y() - gh / 2, gw, gh), glyph, QRectF(glyph.rect()))
        else:
            self._paint_arrows(painter, center, side, base)
        painter.end()

    def _paint_backdrop(self, painter: QPainter, center: QPointF, side: float, base: QColor) -> None:
        radius = side / 2 - 0.5
        fill = QColor(base)
        fill.setAlpha(34 if self._is_light else 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(center, radius, radius)

        if self._mode == ICON_MODE_CHECKING:
            # Пока идёт проверка, по краю круга видна тонкая подсветка.
            ring = QColor(base)
            ring.setAlpha(70 if self._is_light else 90)
            pen = QPen(ring, 1.2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, radius - 0.6, radius - 0.6)

    def _paint_arrows(self, painter: QPainter, center: QPointF, side: float, base: QColor) -> None:
        radius = side * 0.25
        stroke = max(2.0, side * 0.07)
        head_len = stroke * 1.9
        head_half = stroke * 1.35

        pen = QPen(base, stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        head_pen = QPen(base, stroke * 0.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        span = max(40.0, self._span)
        for offset in (0.0, 180.0):
            # Qt считает углы против часовой стрелки; стрелки идут по часовой.
            start = 90.0 - self._angle - offset
            end = start - span
            path = QPainterPath()
            path.arcMoveTo(rect, start)
            path.arcTo(rect, start, -span)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

            # Наконечник: основание лежит на конце дуги поперёк линии,
            # остриё смотрит по касательной вдоль хода по часовой стрелке.
            theta = math.radians(end)
            base_point = QPointF(
                center.x() + radius * math.cos(theta),
                center.y() - radius * math.sin(theta),
            )
            tangent = QPointF(math.sin(theta), math.cos(theta))
            normal = QPointF(-tangent.y(), tangent.x())
            arrow = QPainterPath()
            arrow.moveTo(base_point + tangent * head_len)
            arrow.lineTo(base_point + normal * head_half)
            arrow.lineTo(base_point - normal * head_half)
            arrow.closeSubpath()
            painter.setPen(head_pen)
            painter.setBrush(base)
            painter.drawPath(arrow)

__all__ = [
    "ICON_MODE_CHECKING",
    "ICON_MODE_ERROR",
    "ICON_MODE_IDLE",
    "UpdateSyncIcon",
]
