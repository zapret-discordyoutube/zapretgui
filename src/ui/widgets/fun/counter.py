"""Счётчик-«таблетка», который подпрыгивает, когда число меняется."""

from __future__ import annotations

import math

from PyQt6.QtCore import QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QWidget

from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme_refresh import ThemeRefreshBinding

BUMP_MS = 480


class CounterBadge(QWidget):
    """``[ ✓ 2  надёжно работают ]`` — значение, подпись и тон (success/warning/error/muted)."""

    def __init__(self, caption: str = "", parent=None, *, tone: str = "success", mark: str = "") -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._caption = str(caption or "")
        self._tone = tone
        self._mark = mark
        self._value = 0
        self._t = 1.0
        self._fg = QColor("#6ccb5f")
        self._bg = QColor(108, 203, 95, 40)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(BUMP_MS)
        self._anim.valueChanged.connect(self._on_value)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()
        self._sync()

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        value = int(value)
        if value == self._value:
            return
        grew = value > self._value
        self._value = value
        self._sync()
        if grew and are_live_animations_enabled() and self.isVisible():
            self._anim.stop()
            self._anim.start()

    def set_caption(self, caption: str) -> None:
        self._caption = str(caption or "")
        self._sync()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self._apply_theme_refresh()

    def label_text(self) -> str:
        head = f"{self._mark} {self._value}".strip()
        return f"{head}  {self._caption}".strip()

    def _sync(self) -> None:
        metrics = QFontMetrics(self.font())
        self.setFixedSize(metrics.horizontalAdvance(self.label_text()) + 28, metrics.height() + 12)
        set_state_text(self, f"{self._caption}: {self._value}")
        self.update()

    def _on_value(self, value) -> None:
        self._t = float(value)
        self.update()

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme_semantic import get_semantic_palette

            palette = get_semantic_palette(getattr(tokens, "theme_name", None))
            color = {
                "success": palette.success_text,
                "warning": palette.warning_text,
                "error": palette.error_text,
            }.get(self._tone)
            if color is None:
                from ui.theme import get_theme_tokens

                color = (tokens or get_theme_tokens()).fg_muted
        except Exception:
            color = "#9aa0a6"
        from ui.theme import to_qcolor

        self._fg = to_qcolor(color, "#9aa0a6")
        self._bg = QColor(self._fg)
        self._bg.setAlphaF(0.16)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        # Подпрыгивание: быстро вверх и увеличение, затухающий отскок.
        t = self._t
        bump = math.sin(math.pi * min(1.0, t / 0.5)) if t < 0.5 else 0.25 * math.sin(math.pi * (t - 0.5) / 0.5)
        scale = 1.0 + 0.16 * bump
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.translate(rect.center())
        painter.scale(scale, scale)
        painter.translate(-rect.center())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._bg)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setPen(self._fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.label_text())
        painter.end()


__all__ = ["CounterBadge"]
