"""Ряд из двух частей, который при нехватке ширины переносит вторую часть вниз.

Зачем. Окно рассчитано на 900 px и шире, но при масштабе экрана 125–150 % на
ноутбуке 1280 или 1366 px логическая ширина падает до 850–1000 px. Обычный
QHBoxLayout не умеет «не помещаюсь, встану в две строки»: он продолжает
рисовать части рядом, и они налезают друг на друга (вкладки на переключатель
DoH, кнопки на значок адаптера). Здесь ширину проверяет сам ряд и меняет
направление раскладки: рядом (LeftToRight) или одна под другой (TopToBottom).

Ряд только решает, в одну строку или в две. Что в нём лежит, определяют сами
вызывающие части: ведущая (lead) и замыкающая (trail).
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import QBoxLayout, QLayout, QWidget


def _preferred_width(widget: QWidget) -> int:
    """Желаемая ширина части: sizeHint, а если части нечего сказать, её минимум (как это делает QLayout)."""
    return max(widget.sizeHint().width(), widget.minimumSizeHint().width(), widget.minimumWidth(), 0)


def _smallest_width(widget: QWidget) -> int:
    return max(widget.minimumSizeHint().width(), widget.minimumWidth(), 0)


class ResponsiveRow(QWidget):
    def __init__(
        self,
        lead: QWidget,
        trail: QWidget,
        *,
        spacing: int = 16,
        fill_lead: bool = False,
        lead_width: Callable[[], int] | None = None,
        wide_vertical: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignVCenter,
        narrow_trail_alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignRight,
        parent: QWidget | None = None,
    ) -> None:
        """fill_lead: ведущая часть занимает всю свободную ширину и в одной строке, и
        в двух (нужно ряду с переносом, например значкам адаптеров). Иначе она держит
        свой natural-размер, а замыкающая прижата вправо.
        lead_width: сколько ведущей части нужно, чтобы чувствовать себя свободно;
        по умолчанию её sizeHint. Нужен, когда у части есть перенос строк и sizeHint
        слишком мал (ряд значков адаптеров)."""
        super().__init__(parent)
        self._lead = lead
        self._trail = trail
        self._spacing = int(spacing)
        self._fill_lead = bool(fill_lead)
        self._lead_width = lead_width
        self._wide_vertical = wide_vertical
        self._narrow_trail_alignment = narrow_trail_alignment
        self._narrow: bool | None = None

        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(self._spacing)
        # Окно не должно подтягивать свой минимум к ширине «рядом»: иначе строка никогда не сузится.
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._layout.addWidget(lead)
        self._layout.addWidget(trail)
        self._apply(narrow=False)

    @property
    def is_narrow(self) -> bool:
        return bool(self._narrow)

    def needed_width(self) -> int:
        lead = self._lead_width() if self._lead_width is not None else _preferred_width(self._lead)
        return int(lead) + self._spacing + _preferred_width(self._trail)

    def refresh(self) -> None:
        """Пересчитывает раскладку; вызывать, когда в частях изменился состав."""
        self._apply(narrow=self.width() < self.needed_width())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # По ширине хватает самой широкой из частей: если рядом не влезают, они встанут в два этажа.
        width = max(_smallest_width(self._lead), _smallest_width(self._trail))
        return QSize(width, self._layout.minimumSize().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.refresh()

    def event(self, event) -> bool:
        # Часть сменила размер (текст, показ или скрытие вложенного): пора перепроверить.
        if event.type() == QEvent.Type.LayoutRequest:
            self.refresh()
        return super().event(event)

    def _apply(self, *, narrow: bool) -> None:
        if narrow == self._narrow:
            return
        self._narrow = narrow
        layout = self._layout
        top = Qt.AlignmentFlag.AlignTop
        # Без горизонтального признака часть тянется на всю ширину (нужно ряду с переносом).
        lead_side = Qt.AlignmentFlag(0) if self._fill_lead else Qt.AlignmentFlag.AlignLeft
        if narrow:
            layout.setDirection(QBoxLayout.Direction.TopToBottom)
            layout.setStretchFactor(self._lead, 0)
            layout.setStretchFactor(self._trail, 0)
            layout.setAlignment(self._lead, lead_side | top)
            layout.setAlignment(self._trail, self._narrow_trail_alignment | top)
            return
        layout.setDirection(QBoxLayout.Direction.LeftToRight)
        layout.setStretchFactor(self._lead, 1 if self._fill_lead else 0)
        layout.setStretchFactor(self._trail, 0 if self._fill_lead else 1)
        layout.setAlignment(self._lead, lead_side | self._wide_vertical)
        trail_side = Qt.AlignmentFlag.AlignLeft if self._fill_lead else Qt.AlignmentFlag.AlignRight
        layout.setAlignment(self._trail, trail_side | self._wide_vertical)


__all__ = ["ResponsiveRow"]
