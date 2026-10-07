"""Подпись в одну строку: что не помещается, уходит в многоточие и в подсказку."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QSizePolicy
from qfluentwidgets import CaptionLabel


class ElidedLabel(CaptionLabel):
    """Одна строка: что не помещается, уходит в многоточие и в подсказку."""

    def __init__(self, text: str = "", parent=None, *, align=Qt.AlignmentFlag.AlignLeft, strong: bool = False) -> None:
        # Только с родителем: у FluentLabel вызов с текстом заново зовёт __init__(parent).
        super().__init__(parent)
        if strong:
            font = self.font()
            font.setPixelSize(14)
            font.setWeight(QFont.Weight.DemiBold)
            self.setFont(font)
        self._full = ""
        self._align = align
        self.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.set_full_text(text)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = str(text or "")
        self._elide()

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self._full) + 2, metrics.height() + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, QFontMetrics(self.font()).height() + 2)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        shown = metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, max(8, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


__all__ = ["ElidedLabel"]
