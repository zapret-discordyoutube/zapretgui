"""Блоки в несколько столбцов: сколько помещается по ширине окна."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QLayout, QWidget


class ColumnsFlow(QWidget):
    """Небольшие блоки стоят рядом, а не каждый во всю ширину окна.

    Блок из одной-двух карточек, растянутый на всю страницу, на три четверти
    пуст. Здесь такие блоки делят строку; в узком окне снова идут один под другим.
    """

    # Число столбцов изменилось — высота содержимого стала другой.
    replaced = pyqtSignal()

    def __init__(self, parent=None, *, min_width: int = 380, gap: int = 8) -> None:
        super().__init__(parent)
        self._min_width = int(min_width)
        self._gap = int(gap)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(self._gap * 2)
        self._grid.setVerticalSpacing(self._gap)
        # Ширину задаёт окно: иначе три столбца не дали бы странице сузиться обратно до одного.
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._blocks: list[QWidget] = []
        self._columns = 0

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(200, self._grid.minimumSize().height())

    def blocks(self) -> list[QWidget]:
        return list(self._blocks)

    def columns(self) -> int:
        return self._columns

    def add(self, block: QWidget) -> None:
        block.setParent(self)
        self._blocks.append(block)
        self._place(force=True)

    def columns_for(self, width: int) -> int:
        fit = max(1, (width + self._gap) // (self._min_width + self._gap))
        return max(1, min(fit, len(self._blocks)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()

    def _place(self, *, force: bool = False) -> None:
        columns = self.columns_for(self.width())
        if columns == self._columns and not force:
            return
        for column in range(max(columns, self._columns)):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._columns = columns
        # Сетка не переставляет виджет сама: сначала убрать все места, потом раздать заново.
        while self._grid.count():
            self._grid.takeAt(0)
        for order, block in enumerate(self._blocks):
            self._grid.addWidget(block, order // columns, order % columns, Qt.AlignmentFlag.AlignTop)
        self._grid.invalidate()
        self._grid.activate()
        self.updateGeometry()
        self.replaced.emit()


__all__ = ["ColumnsFlow"]
