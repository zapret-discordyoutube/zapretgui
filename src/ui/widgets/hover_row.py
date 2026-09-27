from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath

from ui.theme import get_theme_tokens, to_qcolor


@dataclass(frozen=True)
class HoverRowPaintResult:
    rect: QRect
    background: QColor


def profile_hover_row_rect(source_rect: QRect) -> QRect:
    """Единая геометрия hover-строки в списках profile/preset/стратегий."""

    return source_rect.adjusted(8, 2, -8, -2)


def paint_profile_hover_row(
    painter: QPainter,
    rect: QRect,
    *,
    active: bool = False,
    hovered: bool = False,
    pressed: bool = False,
    selected: bool = False,
    fill_idle: bool = True,
    show_active_marker: bool = True,
    active_reveal: float | None = None,
    residual_active: float = 0.0,
) -> HoverRowPaintResult:
    """
    Рисует общий фон строки списка.

    Используется там, где строка рисуется через delegate: «Мои пресеты» и
    список готовых стратегий. Так hover, активная подложка и акцентная полоска
    остаются одинаковыми.
    """

    tokens = get_theme_tokens()
    if active and active_reveal is not None and active_reveal < 1.0:
        return _paint_revealing_active_row(painter, rect, tokens, active_reveal, hovered or pressed or selected)
    if not active and residual_active > 0.0:
        result = paint_profile_hover_row(
            painter,
            rect,
            hovered=hovered,
            pressed=pressed,
            selected=selected,
            fill_idle=fill_idle,
        )
        fading = to_qcolor(tokens.accent_soft_bg, tokens.accent_hex)
        fading.setAlphaF(fading.alphaF() * max(0.0, min(1.0, residual_active)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fading)
        painter.drawRoundedRect(rect, 10, 10)
        return result
    if active:
        background = to_qcolor(
            tokens.accent_soft_bg_hover if (hovered or pressed or selected) else tokens.accent_soft_bg,
            tokens.accent_hex,
        )
    elif hovered or pressed or selected:
        background = to_qcolor(tokens.surface_bg_hover, tokens.surface_bg)
    elif fill_idle:
        background = to_qcolor(tokens.surface_bg, "#1f1f1f")
    else:
        background = QColor(0, 0, 0, 0)

    if active or hovered or pressed or selected or fill_idle:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 10, 10)

    if active and show_active_marker:
        marker_rect = QRect(rect.left() + 6, rect.top() + 6, 4, max(12, rect.height() - 12))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(tokens.accent_hex, "#5caee8"))
        painter.drawRoundedRect(marker_rect, 2, 2)

    return HoverRowPaintResult(rect=rect, background=background)


def _paint_revealing_active_row(painter: QPainter, rect: QRect, tokens, reveal: float, highlighted: bool) -> HoverRowPaintResult:
    """Новая активная строка «закрашивается» подсветкой слева направо."""
    base = to_qcolor(tokens.surface_bg_hover if highlighted else tokens.surface_bg, "#1f1f1f")
    active_bg = to_qcolor(tokens.accent_soft_bg_hover if highlighted else tokens.accent_soft_bg, tokens.accent_hex)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(base)
    painter.drawRoundedRect(rect, 10, 10)
    reveal = max(0.0, min(1.0, reveal))
    if reveal > 0.0:
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(rect), 10, 10)
        clip = QPainterPath()
        clip.addRect(QRectF(rect.left(), rect.top(), rect.width() * reveal, rect.height()))
        painter.setBrush(active_bg)
        painter.drawPath(shape.intersected(clip))
    return HoverRowPaintResult(rect=rect, background=active_bg if reveal >= 0.5 else base)


__all__ = [
    "HoverRowPaintResult",
    "paint_profile_hover_row",
    "profile_hover_row_rect",
]
