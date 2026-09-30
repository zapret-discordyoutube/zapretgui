"""Подложка значков в заголовке окна (Premium, состояние Zapret).

Скругление, заданное таблицей стилей, Qt рисует без сглаживания — углы
выходят ступенчатыми. Поэтому фон значка рисуется здесь, кистью со
сглаживанием, а в таблице стилей фон прозрачный.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QPainter, QPainterPath
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme

from ui.theme import to_qcolor


BADGE_RADIUS = 6.0


def current_theme_name() -> str:
    return "dark" if isDarkTheme() else "light"


def badge_shape(widget: QWidget) -> QPainterPath:
    body = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    shape = QPainterPath()
    shape.addRoundedRect(body, BADGE_RADIUS, BADGE_RADIUS)
    return shape


def paint_badge_body(widget: QWidget, *, background: str, hover_background: str) -> None:
    """Заливает значок: при наведении — цветом наведения."""
    hovered = bool(getattr(widget, "isHover", False)) and not bool(getattr(widget, "isPressed", False))
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(badge_shape(widget), to_qcolor(hover_background if hovered else background))
    painter.end()


def badge_qss(selector: str, *, fg: str, left_padding: int, disabled: bool = False) -> str:
    """Стиль текста значка. Фон прозрачный — его рисует paint_badge_body."""
    qss = (
        f"{selector} {{ color: {fg}; background: transparent; border: none; "
        f"padding: 0px 8px 0px {left_padding}px; font-size: 10px; font-weight: 600; }}"
        f"{selector}:hover {{ background: transparent; }}"
        f"{selector}:pressed {{ background: transparent; }}"
    )
    if disabled:
        qss += f"{selector}:disabled {{ color: {fg}; background: transparent; }}"
    return qss


__all__ = ["BADGE_RADIUS", "badge_qss", "badge_shape", "current_theme_name", "paint_badge_body"]
