"""Значок типов пакетов составной готовой стратегии («TLS · HTTP · TLS+HTTP»).

Рисуется в списке profile-ов и в списке готовых стратегий одинаково:
мягкая подложка без рамки и приглушённый текст (интерфейс без рамок).
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QFontMetrics, QPainter

from ui.theme import to_qcolor

PAYLOAD_BADGE_HEIGHT = 18
_PAYLOAD_BADGE_H_PADDING = 7


def payload_badge_width(metrics: QFontMetrics, text: str) -> int:
    clean = str(text or "").strip()
    if not clean:
        return 0
    return metrics.horizontalAdvance(clean) + _PAYLOAD_BADGE_H_PADDING * 2


def paint_payload_badge(painter: QPainter, rect: QRect, text: str, metrics: QFontMetrics, tokens) -> None:
    clean = str(text or "").strip()
    if not clean or not rect.isValid():
        return
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(to_qcolor(tokens.surface_bg_hover, "#3a3a3a"))
    painter.drawRoundedRect(rect, rect.height() // 2, rect.height() // 2)
    painter.setPen(to_qcolor(tokens.fg_muted, "#b7bec8"))
    painter.drawText(
        rect,
        int(Qt.AlignmentFlag.AlignCenter),
        metrics.elidedText(clean, Qt.TextElideMode.ElideRight, max(0, rect.width() - _PAYLOAD_BADGE_H_PADDING)),
    )


__all__ = [
    "PAYLOAD_BADGE_HEIGHT",
    "paint_payload_badge",
    "payload_badge_width",
]
