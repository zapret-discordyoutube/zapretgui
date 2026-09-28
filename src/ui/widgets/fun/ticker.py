"""Строка с весёлыми фразами, пока идёт работа.

Фразы меняются раз в несколько секунд: старая уплывает вверх и тает, новая
выплывает снизу. Подряд одна и та же фраза не повторяется. Без «живых
анимаций» фразы меняются без движения. Остановленный тикер ничего не делает.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from PyQt6.QtCore import QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme_refresh import ThemeRefreshBinding

TICKER_INTERVAL_MS = 2800
TICKER_SWAP_MS = 420


class FunTicker(QWidget):
    def __init__(self, parent=None, *, seed=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        font = self.font()
        font.setItalic(True)
        self.setFont(font)
        self.setFixedHeight(QFontMetrics(font).height() + 8)
        self._rng = random.Random(seed)
        self._phrases: list[str] = []
        self._text = ""
        self._previous = ""
        self._t = 1.0
        self._color = QColor("#9aa0a6")

        self._timer = QTimer(self)
        self._timer.setInterval(TICKER_INTERVAL_MS)
        self._timer.timeout.connect(self.next_phrase)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(TICKER_SWAP_MS)
        self._anim.valueChanged.connect(self._on_value)
        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()

    # --- управление -----------------------------------------------------------

    def text(self) -> str:
        return self._text

    def is_running(self) -> bool:
        return self._timer.isActive()

    def set_phrases(self, phrases: Sequence[str]) -> None:
        """Новый набор фраз; если тикер работает, сразу показывает одну из них."""
        cleaned = [str(item) for item in phrases if str(item or "").strip()]
        if cleaned == self._phrases:
            return
        self._phrases = cleaned
        if self.is_running():
            self.next_phrase()

    def start(self) -> None:
        if not self._phrases:
            return
        self.next_phrase()
        self._timer.start()

    def stop(self, final_text: str = "") -> None:
        self._timer.stop()
        self._anim.stop()
        self._set_text(final_text, animate=False)

    def next_phrase(self) -> None:
        if not self._phrases:
            return
        choices = [item for item in self._phrases if item != self._text] or self._phrases
        self._set_text(self._rng.choice(choices), animate=True)

    def _set_text(self, text: str, *, animate: bool) -> None:
        self._previous = self._text
        self._text = str(text or "")
        set_state_text(self, self._text)
        if animate and self._previous and are_live_animations_enabled() and self.isVisible():
            self._t = 0.0
            self._anim.stop()
            self._anim.start()
        else:
            self._t = 1.0
            self.update()

    def _on_value(self, value) -> None:
        self._t = float(value)
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self._t = 1.0
        super().hideEvent(event)

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme import get_theme_tokens

            from ui.theme import to_qcolor

            tokens = tokens or get_theme_tokens()
            self._color = to_qcolor(tokens.fg_muted, "#9aa0a6")
        except Exception:
            self._color = QColor("#9aa0a6")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font())
        rect = QRectF(self.rect())
        shift = rect.height() * 0.7
        metrics = QFontMetrics(self.font())
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        def draw(text: str, dy: float, alpha: float) -> None:
            if not text or alpha <= 0:
                return
            color = QColor(self._color)
            color.setAlphaF(max(0.0, min(1.0, alpha)))
            painter.setPen(color)
            elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width()))
            painter.drawText(rect.translated(0, dy), flags, elided)

        t = self._t
        if t < 1.0:
            draw(self._previous, -shift * t, 1.0 - t)
        draw(self._text, shift * (1.0 - t), t)
        painter.end()


__all__ = ["FunTicker", "TICKER_INTERVAL_MS"]
