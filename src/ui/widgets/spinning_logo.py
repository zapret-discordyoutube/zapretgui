"""Логотип программы, который по клику делает оборот, как значок в Chrome.

Виджет только рисует готовый QIcon и крутит его. Сам значок программы задаёт
main/qt_runtime.py, сюда он приходит уже готовым через QApplication.windowIcon().
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled


# Непрозрачные края логотипа (ухо, хвост) выходят за вписанный круг
# примерно на 9 %. Рисуем значок чуть меньше коробки, чтобы при повороте
# углы не обрезались краем виджета.
LOGO_ICON_RATIO = 0.9
SPIN_TURN_DEGREES = 360.0
SPIN_DURATION_MS = 900
PRESSED_SCALE = 0.86


class SpinningLogo(QWidget):
    """Значок, который по клику плавно делает полный оборот.

    Повторные клики во время вращения добавляют ещё по обороту, поэтому
    значок можно «раскрутить». Если в настройках анимации выключены,
    значок стоит на месте.
    """

    clicked = pyqtSignal()

    def __init__(
        self,
        icon: QIcon | None = None,
        *,
        box_size: int = 20,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._icon = QIcon() if icon is None else QIcon(icon)
        self._angle = 0.0
        self._target_angle = 0.0
        self._pressed = False
        self._cached_pixmap: QPixmap | None = None
        self._cached_key: tuple[int, float] | None = None

        side = max(8, int(box_size))
        self.setFixedSize(side, side)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start.
        self._spin = QVariantAnimation(self)
        self._spin.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._spin.valueChanged.connect(self._on_spin_value)
        self._spin.finished.connect(self._on_spin_finished)

        set_control_accessibility(
            self,
            name="Логотип Zapret 2",
            description="Нажмите, чтобы покрутить логотип.",
        )

    # ---- публичное API -------------------------------------------------

    def set_icon(self, icon: QIcon) -> None:
        self._icon = QIcon(icon)
        self._cached_pixmap = None
        self._cached_key = None
        self.update()

    def icon(self) -> QIcon:
        return QIcon(self._icon)

    def angle(self) -> float:
        return self._angle

    def is_spinning(self) -> bool:
        return self._spin.state() == QVariantAnimation.State.Running

    def spin(self) -> None:
        """Добавляет один оборот к текущему вращению."""
        if not are_live_animations_enabled():
            return

        was_running = self.is_spinning()
        base_target = self._target_angle if was_running else self._angle
        self._spin.stop()
        self._target_angle = base_target + SPIN_TURN_DEGREES
        self._spin.setStartValue(float(self._angle))
        self._spin.setEndValue(float(self._target_angle))
        self._spin.setDuration(SPIN_DURATION_MS)
        self._spin.start()

    # ---- события -------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        # Принимаем событие, чтобы верхняя панель не начала тащить окно.
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        was_pressed = self._pressed
        self._pressed = False
        self.update()
        event.accept()
        if was_pressed and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.spin()
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Двойной клик по верхней панели разворачивает окно; по логотипу —
        # это просто ещё одно нажатие.
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._spin.stop()
        self._angle = 0.0
        self._target_angle = 0.0
        self._pressed = False
        super().hideEvent(event)

    def _on_spin_value(self, value) -> None:
        try:
            self._angle = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_spin_finished(self) -> None:
        self._angle = 0.0
        self._target_angle = 0.0
        self.update()

    # ---- отрисовка -----------------------------------------------------

    def _icon_pixmap(self, icon_side: int) -> QPixmap | None:
        if self._icon.isNull():
            return None
        dpr = float(self.devicePixelRatioF() or 1.0)
        key = (icon_side, dpr)
        if self._cached_pixmap is None or self._cached_key != key:
            physical = max(1, round(icon_side * dpr))
            pixmap = self._icon.pixmap(physical, physical)
            if pixmap.isNull():
                return None
            if pixmap.width() != physical:
                pixmap = pixmap.scaled(
                    physical,
                    physical,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            pixmap.setDevicePixelRatio(dpr)
            self._cached_pixmap = pixmap
            self._cached_key = key
        return self._cached_pixmap

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        side = min(self.width(), self.height())
        icon_side = max(1, round(side * LOGO_ICON_RATIO))
        pixmap = self._icon_pixmap(icon_side)
        if pixmap is None:
            return

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.translate(QPointF(self.width() / 2, self.height() / 2))
        painter.rotate(self._angle % 360.0)
        if self._pressed:
            painter.scale(PRESSED_SCALE, PRESSED_SCALE)
        half = icon_side / 2
        painter.drawPixmap(QRectF(-half, -half, icon_side, icon_side), pixmap, QRectF(pixmap.rect()))
        painter.end()


__all__ = ["SpinningLogo"]
