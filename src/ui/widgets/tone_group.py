"""Цветная группа: метка-заголовок, пояснение и строки на мягкой подложке.

Так экраны проверок собирают однородные строки: в BlockCheck — проблемы
одного вида блокировки, во вкладке «DNS-серверы» — находки одной важности.
У группы свой цвет: им окрашены метка, полоска слева и едва заметная подложка.
Рамок нет — окно программы без рамок, состояние показывают фон и метка.

Группа не знает, что в ней лежит: экран даёт название, функцию цвета и
добавляет свои строки через ``add_widget``.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from ui.theme_refresh import ThemeRefreshBinding

# Функция цвета получает токены темы (или None) и возвращает цвет строкой.
ColorFor = Callable[[object], str]


class TonePill(QLabel):
    """Цветная метка-«таблетка»: название группы."""

    def __init__(self, text: str, color_for: ColorFor, parent=None) -> None:
        super().__init__(text, parent)
        self._color_for = color_for
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Скругление в QSS работает, только пока радиус не больше половины высоты.
        self.setFixedHeight(22)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        color = QColor(self._color_for(tokens))
        self.setStyleSheet(
            f"QLabel {{ color: {color.name()}; "
            f"background-color: rgba({color.red()}, {color.green()}, {color.blue()}, 0.16); "
            "border-radius: 10px; padding: 0px 10px; font-weight: 600; }"
        )


class ToneGroup(QWidget):
    """Группа строк одного цвета.

    ``plain`` — группа без названия и подложки: строки идут как обычный список
    (для того, что ни к какому виду не отнесли).
    """

    def __init__(
        self,
        title: str,
        color_for: ColorFor,
        parent=None,
        *,
        count: int = 0,
        about: str = "",
        plain: bool = False,
    ) -> None:
        super().__init__(parent)
        self._color_for = color_for
        self._plain = plain
        self._color = QColor()
        self._body = QVBoxLayout(self)
        self._body.setSpacing(6)
        if plain:
            self._body.setContentsMargins(0, 0, 0, 0)
        else:
            self._body.setContentsMargins(16, 10, 12, 10)

        self.pill: TonePill | None = None
        self.about_label: CaptionLabel | None = None
        if not plain:
            header = QHBoxLayout()
            header.setSpacing(8)
            self.pill = TonePill(title, color_for, self)
            header.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
            if count > 1:
                header.addWidget(CaptionLabel(f"· {count}", self), 0, Qt.AlignmentFlag.AlignVCenter)
            header.addStretch(1)
            self._body.addLayout(header)
            if about:
                self.about_label = self.add_note(about)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_note(self, text: str) -> CaptionLabel:
        """Строка мелкого текста во всю ширину группы: пояснение или общий совет."""
        label = CaptionLabel(text, self)
        label.setWordWrap(True)
        self._body.addWidget(label)
        return label

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        self._color = QColor(self._color_for(tokens))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        if self._plain:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        fill = QColor(self._color)
        fill.setAlphaF(0.07)
        painter.setBrush(fill)
        painter.drawRoundedRect(self.rect(), 8, 8)
        painter.setBrush(self._color)
        painter.drawRoundedRect(0, 10, 3, max(0, self.height() - 20), 1.5, 1.5)
        painter.end()


__all__ = ["ToneGroup", "TonePill"]
