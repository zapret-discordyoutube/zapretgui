"""Подсказка под мышью для виджета, который рисует много элементов сам.

Обычная подсказка (``ui.fluent_widgets.set_tooltip``) одна на весь виджет. У
таблицы или сетки, нарисованной одним виджетом, текст свой у каждой строки:
виджет сам говорит, что показать и где. Окно подсказки — то же, что у
остальной программы (``qfluentwidgets.ToolTip``). Системное ``QToolTip`` здесь
не годится: на тёмной теме оно на миг появляется белым прямоугольником.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QPoint, QTimer
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import ToolTip

_OFFSET = QPoint(14, 20)


class HoverHint(QObject):
    """Показывает подсказку с задержкой и прячет её, когда мышь ушла с элемента."""

    def __init__(self, widget: QWidget, *, delay_ms: int = 350) -> None:
        super().__init__(widget)
        self._widget = widget
        self._tip: ToolTip | None = None
        self._text = ""
        self._pos = QPoint()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(delay_ms))
        self._timer.timeout.connect(self._show)

    def text(self) -> str:
        """Текст подсказки, которая показана или ждёт показа. Пусто — подсказки нет."""
        return self._text

    def show(self, text: str, global_pos: QPoint) -> None:
        text = str(text or "")
        if not text:
            self.hide()
            return
        if text == self._text:
            return
        self.hide()
        self._text = text
        self._pos = QPoint(global_pos)
        self._timer.start()

    def hide(self) -> None:
        self._timer.stop()
        self._text = ""
        if self._tip is not None:
            self._tip.hide()

    def _show(self) -> None:
        if not self._text or not self._widget.isVisible():
            return
        if self._tip is None:
            # Окно подсказки удаляется вместе с виджетом.
            self._tip = ToolTip(self._text, self._widget.window())
            self._widget.destroyed.connect(self._tip.deleteLater)
        self._tip.setText(self._text)
        self._tip.setDuration(8000)
        self._tip.adjustSize()
        self._tip.move(self._pos + _OFFSET)
        self._tip.show()


__all__ = ["HoverHint"]
