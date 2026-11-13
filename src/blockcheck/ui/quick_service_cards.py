"""Карточки самых известных заблокированных сервисов: логотип и название, выбор одним нажатием.

Заменяют поиск адреса в текстовом меню: YouTube, Discord, Telegram и другие всегда на виду.
Карточка только сообщает, какой сервис выбрали: ``picked`` — его адрес (подбор стратегии подставляет
его в поле), ``chosen`` — его ключ (BlockCheck проверяет этот сервис целиком).
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QGridLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel

from blockcheck.quick_services import QUICK_SERVICES, QuickService, service_for_target
from blockcheck.ui.brand_icons import BrandIcon, site_brand
from ui.accessibility import (
    enable_keyboard_click,
    set_control_accessibility,
    set_state_text,
)
from ui.fluent_widgets import set_tooltip
from ui.theme_refresh import ThemeRefreshBinding

# Сколько карточек в строке: две ровные строки по шесть.
COLUMNS = 6
LOGO_SIZE = 30


def _tokens():
    try:
        from ui.theme import get_theme_tokens

        return get_theme_tokens()
    except Exception:
        return None


class ServiceCard(QWidget):
    """Одна карточка: логотип в фирменном цвете над названием."""

    clicked = pyqtSignal()

    def __init__(self, service: QuickService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMinimumSize(96, 78)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._selected = False
        self._hover = False
        self._colors: dict[str, QColor] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)
        brand = site_brand(service.key, service.target)
        self.logo = BrandIcon(brand.icon if brand else "fa5s.globe", brand.color if brand else "", self, size=LOGO_SIZE)
        self.name = CaptionLabel(service.title, self)
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.name, 0, Qt.AlignmentFlag.AlignHCenter)
        set_tooltip(self, service.hint or service.title)
        enable_keyboard_click(self)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()
        self._announce()

    def click(self) -> None:
        if self.isEnabled():
            self.clicked.emit()

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._announce()
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    def _announce(self) -> None:
        state = "выбрано" if self._selected else "не выбрано"
        text = f"Быстрая цель: {self.service.title}, {state}"
        set_control_accessibility(self, name=text, description=self.service.hint or self.service.title)
        set_state_text(self, text)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.click()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        from ui.theme import to_qcolor

        tokens = tokens or _tokens()
        self._colors = {
            "accent": to_qcolor(getattr(tokens, "accent_hex", "#60cdff"), "#60cdff"),
            "soft": to_qcolor(getattr(tokens, "accent_soft_bg", None), "#2060cdff"),
            "bg": to_qcolor(getattr(tokens, "surface_bg", None), "#10ffffff"),
            "hover": to_qcolor(getattr(tokens, "surface_bg_hover", None), "#18ffffff"),
        }
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._selected:
            fill = QColor(self._colors.get("soft", QColor(96, 205, 255, 40)))
        elif self._hover and self.isEnabled():
            fill = QColor(self._colors.get("hover", QColor(255, 255, 255, 20)))
        else:
            fill = QColor(self._colors.get("bg", QColor(255, 255, 255, 10)))
        if not self.isEnabled():
            fill.setAlphaF(fill.alphaF() * 0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 8, 8)
        if self._selected:
            accent = QColor(self._colors.get("accent", QColor("#60cdff")))
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(rect.left() + 14, rect.bottom() - 4, rect.width() - 28, 2.5), 1.25, 1.25)
        painter.end()
        super().paintEvent(event)


class QuickServiceCards(QWidget):
    """Сетка карточек сервисов. ``picked(адрес)`` и ``chosen(ключ)`` — нажали на карточку; ``set_target`` отмечает выбранную."""

    picked = pyqtSignal(str)
    chosen = pyqtSignal(str)

    def __init__(self, parent=None, *, services: tuple[QuickService, ...] = QUICK_SERVICES) -> None:
        super().__init__(parent)
        self.setObjectName("quickServiceCards")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self._cards: list[ServiceCard] = []
        for index, service in enumerate(services):
            card = ServiceCard(service, self)
            card.clicked.connect(lambda service=service: self._pick(service))
            grid.addWidget(card, index // COLUMNS, index % COLUMNS)
            self._cards.append(card)
        for column in range(COLUMNS):
            grid.setColumnStretch(column, 1)
        set_control_accessibility(
            self,
            name="Быстрый выбор сервиса",
            description="Карточки известных сервисов: YouTube, Discord, Telegram и других. Нажатие подставляет адрес сервиса.",
        )

    def _pick(self, service: QuickService) -> None:
        self.picked.emit(service.target)
        self.chosen.emit(service.key)

    def cards(self) -> list[ServiceCard]:
        return list(self._cards)

    def set_target(self, value: str) -> None:
        """Отмечает карточку, чей адрес стоит в поле; для чужого адреса не отмечена ни одна."""
        current = service_for_target(value)
        for card in self._cards:
            card.set_selected(current is not None and card.service.key == current.key)

    def selected_key(self) -> str:
        return next((card.service.key for card in self._cards if card.is_selected()), "")
