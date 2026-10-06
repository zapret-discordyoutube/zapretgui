"""Полоса под списком готовых стратегий: что выбрано и как это оценить.

Оценка («работает», «не работает») и избранное относятся к выбранной
стратегии, поэтому стоят там же, где её выбирают. Полоса только показывает
кнопки; что сохранять, решает страница профиля.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget
from qfluentwidgets import BodyLabel, FluentIcon, TogglePushButton

from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import set_tooltip


NO_STRATEGY_TEXT = "Стратегия не выбрана"
CUSTOM_STRATEGY_TEXT = "Своя стратегия"


def visible_strategy_name(strategy_id: str, strategy_name: str) -> str:
    """Как назвать выбранную стратегию человеку."""
    clean_strategy_id = str(strategy_id or "").strip()
    clean_strategy_name = str(strategy_name or "").strip()
    if not clean_strategy_name or clean_strategy_id in {"", "none"}:
        return NO_STRATEGY_TEXT
    if clean_strategy_id == "custom":
        return CUSTOM_STRATEGY_TEXT
    return clean_strategy_name


class StrategyFeedbackBar(QWidget):
    """Название выбранной стратегии и кнопки её оценки."""

    # Нажата «Работает» ("work") или «Не работает» ("notwork").
    rating_clicked = pyqtSignal(str)
    favorite_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.name_label = BodyLabel(NO_STRATEGY_TEXT, self)
        # Длинное название не раздвигает окно: оно обрезается, полное — в подсказке.
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name_label.setMinimumWidth(80)
        layout.addWidget(self.name_label, 1)

        self.work_button = TogglePushButton(FluentIcon.ACCEPT, "Работает", self)
        set_tooltip(
            self.work_button,
            "Отметить, что выбранная стратегия у вас работает. Повторное нажатие снимает отметку.",
        )
        set_control_accessibility(
            self.work_button,
            name="Отметить стратегию как рабочую",
            description="Помечает выбранную готовую стратегию как рабочую. Повторное нажатие снимает отметку.",
        )
        self.work_button.clicked.connect(lambda: self.rating_clicked.emit("work"))
        layout.addWidget(self.work_button)

        self.notwork_button = TogglePushButton(FluentIcon.CLOSE, "Не работает", self)
        set_tooltip(
            self.notwork_button,
            "Отметить, что выбранная стратегия у вас не работает. Повторное нажатие снимает отметку.",
        )
        set_control_accessibility(
            self.notwork_button,
            name="Отметить стратегию как нерабочую",
            description="Помечает выбранную готовую стратегию как нерабочую. Повторное нажатие снимает отметку.",
        )
        self.notwork_button.clicked.connect(lambda: self.rating_clicked.emit("notwork"))
        layout.addWidget(self.notwork_button)

        self.favorite_button = TogglePushButton(FluentIcon.HEART, "В избранное", self)
        set_tooltip(
            self.favorite_button,
            "Добавить выбранную стратегию в избранное или убрать её оттуда. Избранные стоят первыми в своей группе.",
        )
        set_control_accessibility(
            self.favorite_button,
            name="Добавить стратегию в избранное",
            description="Добавляет выбранную готовую стратегию в избранное или убирает её оттуда.",
        )
        self.favorite_button.clicked.connect(lambda: self.favorite_clicked.emit())
        layout.addWidget(self.favorite_button)

    def set_strategy_name(self, name: str) -> None:
        name = str(name or "").strip() or NO_STRATEGY_TEXT
        if self.name_label.text() != name:
            self.name_label.setText(name)
            set_tooltip(self.name_label, name)


__all__ = [
    "CUSTOM_STRATEGY_TEXT",
    "NO_STRATEGY_TEXT",
    "StrategyFeedbackBar",
    "visible_strategy_name",
]
