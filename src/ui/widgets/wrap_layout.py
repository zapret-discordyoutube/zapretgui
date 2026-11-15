"""Раскладка «в строку, а не помещается — на следующую» для рядов кнопок.

Почему не FlowLayout из qfluentwidgets: он пропускает виджеты, которые ещё не
показаны (isVisible), поэтому ряд кнопок, где «Проверить» и «Остановить»
по очереди скрываются, считает высоту до показа окна неверно. Здесь скрытые
виджеты пропускает сам Qt (QLayoutItem.isEmpty), а набор вызовов совпадает с
QHBoxLayout, которым ряд был раньше: addWidget, insertWidget, addStretch.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QWidget, QWidgetItem


class WrapLayout(QLayout):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        horizontal_spacing: int = 10,
        vertical_spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_space = int(horizontal_spacing)
        self._v_space = int(vertical_spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # ── набор вызовов как у QHBoxLayout ───────────────────────

    def addWidget(self, widget: QWidget, *_ignored) -> None:  # noqa: N802
        """Лишние аргументы (stretch, alignment) принимаются, чтобы код ряда не менялся."""
        self.addChildWidget(widget)
        self._items.append(QWidgetItem(widget))
        self.invalidate()

    def insertWidget(self, index: int, widget: QWidget, *_ignored) -> None:  # noqa: N802
        self.addChildWidget(widget)
        position = len(self._items) if index < 0 else min(index, len(self._items))
        self._items.insert(position, QWidgetItem(widget))
        self.invalidate()

    def addStretch(self, _stretch: int = 0) -> None:  # noqa: N802
        """Пустое место справа в строке не нужно: элементы и так стоят слева."""

    # ── QLayout ───────────────────────────────────────────────

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            self.invalidate()
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout_rows(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout_rows(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        # Ширина — самый широкий элемент (всё остальное перенесётся), высота — по одному в строке.
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # ── сама раскладка ────────────────────────────────────────

    def _layout_rows(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x = area.x()
                y += row_height + self._v_space
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h_space
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


__all__ = ["WrapLayout"]
