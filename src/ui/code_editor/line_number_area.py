"""Колонка номеров строк — рисуется самим редактором."""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QWidget


class LineNumberArea(QWidget):
    """Пустой виджет-холст: всю отрисовку делает CodeEditor."""

    def __init__(self, editor) -> None:
        super().__init__(editor)
        self._editor = editor
        self.setProperty("noDrag", True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802
        self._editor.paint_line_numbers(event)
