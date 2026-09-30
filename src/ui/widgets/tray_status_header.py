"""Шапка меню трея: цветная точка, режим, состояние Zapret и активный пресет."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, StrongBodyLabel


class TrayStatusHeader(QWidget):
    """Шапка меню трея: цветная точка, режим, состояние и активный пресет.

    По клику открывает окно программы.
    """

    DOT_SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)
        self.dot_label = QLabel(self)
        self.dot_label.setFixedSize(self.DOT_SIZE, self.DOT_SIZE)
        layout.addWidget(self.dot_label, 0, Qt.AlignmentFlag.AlignVCenter)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        self.title_label = StrongBodyLabel(self)
        self.preset_label = CaptionLabel(self)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.preset_label)
        layout.addLayout(text_layout, 1)
        self._color = ""

    def set_status(self, *, title: str, preset: str, color: str | None) -> None:
        self.title_label.setText(title)
        self.preset_label.setText(preset)
        self.preset_label.setVisible(bool(preset))
        color = color or "#9aa0a6"
        if color != self._color:
            self._color = color
            self.dot_label.setPixmap(self._dot_pixmap(color))

    def _dot_pixmap(self, color: str) -> QPixmap:
        ratio = 2.0
        size = self.DOT_SIZE
        pixmap = QPixmap(int(size * ratio), int(size * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        halo = QColor(color)
        halo.setAlphaF(0.3)
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(0, 0, size, size))
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(2.5, 2.5, size - 5, size - 5))
        painter.end()
        return pixmap


__all__ = ["TrayStatusHeader"]
