from __future__ import annotations

from dataclasses import dataclass
import math

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath

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
    hover_level: float | None = None,
) -> HoverRowPaintResult:
    """
    Рисует общий фон строки списка.

    Используется там, где строка рисуется через delegate: «Мои пресеты» и
    список готовых стратегий. Так hover, активная подложка и акцентная полоска
    остаются одинаковыми.

    hover_level (0..1) — насколько проявлена подсветка наведения при плавном
    наведении (ui/widgets/row_hover_motion.py); None — мгновенно по hovered.
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
            hover_level=hover_level,
        )
        fading = to_qcolor(tokens.accent_soft_bg, tokens.accent_hex)
        fading.setAlphaF(fading.alphaF() * max(0.0, min(1.0, residual_active)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fading)
        painter.drawRoundedRect(rect, 10, 10)
        return result
    if pressed or selected:
        hover_amount = 1.0
    elif hover_level is not None:
        hover_amount = max(0.0, min(1.0, float(hover_level)))
    else:
        hover_amount = 1.0 if hovered else 0.0

    if active:
        background = _mix(
            to_qcolor(tokens.accent_soft_bg, tokens.accent_hex),
            to_qcolor(tokens.accent_soft_bg_hover, tokens.accent_hex),
            hover_amount,
        )
    else:
        idle = to_qcolor(tokens.surface_bg, "#1f1f1f") if fill_idle else QColor(0, 0, 0, 0)
        background = _mix(idle, to_qcolor(tokens.surface_bg_hover, tokens.surface_bg), hover_amount)

    if active or hover_amount > 0.0 or fill_idle:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 10, 10)

    if active and show_active_marker:
        marker_rect = QRect(rect.left() + 6, rect.top() + 6, 4, max(12, rect.height() - 12))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(to_qcolor(tokens.accent_hex, "#5caee8"))
        painter.drawRoundedRect(marker_rect, 2, 2)

    return HoverRowPaintResult(rect=rect, background=background)


def _mix(idle: QColor, hover: QColor, amount: float) -> QColor:
    """Плавный переход цвета подсветки; от прозрачного — только по прозрачности."""
    if amount <= 0.0:
        return QColor(idle)
    if amount >= 1.0:
        return QColor(hover)
    if idle.alpha() == 0:
        result = QColor(hover)
        result.setAlphaF(hover.alphaF() * amount)
        return result
    return QColor.fromRgbF(
        idle.redF() + (hover.redF() - idle.redF()) * amount,
        idle.greenF() + (hover.greenF() - idle.greenF()) * amount,
        idle.blueF() + (hover.blueF() - idle.blueF()) * amount,
        idle.alphaF() + (hover.alphaF() - idle.alphaF()) * amount,
    )


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
