"""Единый переключатель программы: ползунок не сдвигается при смене подписи.

У стандартного qfluentwidgets.SwitchButton подпись («Вкл.»/«Выкл.») стоит
справа от ползунка, и ширина всего переключателя зависит от длины слова.
В строке настроек переключатель прижат к правому краю, поэтому при смене
состояния и между соседними строками ползунок «гулял» по горизонтали.

Здесь ширина подписи фиксирована по самому длинному из двух слов, так что
ползунок и подпись всегда стоят на одном месте. Все переключатели программы
создаются только через этот класс (проверяет app/architecture_checks.py).
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from qfluentwidgets import SwitchButton

from ui.accessibility import remove_switch_indicators_from_tab_order

SWITCH_ON_TEXT = "Вкл."
SWITCH_OFF_TEXT = "Выкл."

_LABEL_METRIC_EVENTS = frozenset(
    {
        QEvent.Type.FontChange,
        QEvent.Type.StyleChange,
        QEvent.Type.Polish,
    }
)


class AlignedSwitchButton(SwitchButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.installEventFilter(self)
        self.setOnText(SWITCH_ON_TEXT)
        self.setOffText(SWITCH_OFF_TEXT)
        # Служебный ползунок-индикатор не должен быть отдельной остановкой Tab:
        # имя для диктора и управление с клавиатуры задаёт владелец переключателя.
        remove_switch_indicators_from_tab_order(self)

    def setOnText(self, text):  # noqa: N802 - имя из qfluentwidgets
        super().setOnText(text)
        self._sync_label_width()

    def setOffText(self, text):  # noqa: N802 - имя из qfluentwidgets
        super().setOffText(text)
        self._sync_label_width()

    def eventFilter(self, obj, e):  # noqa: N802 - имя из Qt
        if obj is self.label and e.type() in _LABEL_METRIC_EVENTS:
            self._sync_label_width()
        return super().eventFilter(obj, e)

    def _sync_label_width(self) -> None:
        label = self.label
        metrics = label.fontMetrics()
        width = max(
            metrics.horizontalAdvance(str(text or ""))
            for text in (self.onText, self.offText, label.text())
        )
        if label.width() != width or label.minimumWidth() != width:
            label.setFixedWidth(width)
            self.adjustSize()


__all__ = ["SWITCH_OFF_TEXT", "SWITCH_ON_TEXT", "AlignedSwitchButton"]
