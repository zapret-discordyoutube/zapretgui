"""Раздел страницы без своей подложки — для содержимого, которое само состоит из карточек."""

from __future__ import annotations

from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget


class FlatSection(QWidget):
    """Контейнер с теми же вызовами, что у ``SettingsCard``, но без фона и рамки.

    Правило экрана: подложка есть только у самой карточки. Карточка на подложке
    раздела, а тот на подложке страницы — «виджет в виджете», читать тяжело.
    """

    def __init__(self, parent=None, *, spacing: int = 12) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Заголовка у раздела нет: его роль играет подпись внутри содержимого.
        self._title_label = None
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(spacing)

    def add_widget(self, widget: QWidget) -> None:
        self.main_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.main_layout.addLayout(layout)


__all__ = ["FlatSection"]
