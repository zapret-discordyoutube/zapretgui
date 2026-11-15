"""Полоса из одной строки, которая прокручивается вбок, когда не помещается.

Нужна вкладкам групп DNS (SegmentedWidget): их ~650–790 px, а при масштабе
экрана 125–150 % страница бывает уже. Перенести вкладки на вторую строку
нельзя, а обрезать их молча — значит спрятать часть групп. Полоса показывает
всё, что помещается, остальное достаётся колесом мыши или жестом, а выбранная
вкладка всегда прокручивается в видимую часть.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QWidget
from qfluentwidgets import SingleDirectionScrollArea

MIN_VISIBLE_WIDTH = 120


class ScrollingStrip(SingleDirectionScrollArea):
    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent, orient=Qt.Orientation.Horizontal)
        self._content = content
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(False)
        self.enableTransparentBackground()
        self.setWidget(content)
        # Подпись вкладки или шрифт сменились: размер содержимого пересчитывается сам.
        content.installEventFilter(self)
        self.sync()

    def sync(self) -> None:
        """Подгоняет размер содержимого под его желаемый; звать после смены состава вкладок."""
        hint = self._content.sizeHint()
        if hint.isValid():
            self._content.resize(hint)
            self.setFixedHeight(hint.height())
            self.updateGeometry()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._content and event.type() == QEvent.Type.LayoutRequest:
            QTimer.singleShot(0, self.sync)
        return super().eventFilter(watched, event)

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = self._content.sizeHint()
        return QSize(max(hint.width(), 0), max(hint.height(), 0))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(min(MIN_VISIBLE_WIDTH, max(self._content.sizeHint().width(), 0)), self.height())

    def ensure_visible(self, widget: QWidget | None) -> None:
        if widget is not None:
            self.ensureWidgetVisible(widget, 24, 0)


__all__ = ["ScrollingStrip"]
