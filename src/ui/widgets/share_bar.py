"""Полоса из цветных долей: какая часть списка в каком состоянии.

Одна полоса на все экраны проверок: в BlockCheck она показывает, сколько
сайтов открывается и чем мешают остальным, во вкладке «DNS-серверы» —
сколько серверов работает, скольким мешают и сколько молчит.

Экран отдаёт полосе доли (ключ и число) и функцию «ключ → цвет»; сама полоса
про сайты и серверы ничего не знает. Доли заполняются слева направо меньше
чем за секунду, и только когда включены «живые анимации».
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ui.animation_policy import are_live_animations_enabled
from ui.theme_refresh import ThemeRefreshBinding

REVEAL_MS = 760

ColorOf = Callable[[str], "QColor | str"]


def _is_light() -> bool:
    try:
        from ui.theme import get_theme_tokens

        return bool(get_theme_tokens().is_light)
    except Exception:
        return False


class ShareBar(QWidget):
    """Скруглённая полоса: доли идут слева направо в том порядке, в каком их дали."""

    HEIGHT = 6
    GAP = 2.0

    def __init__(self, color_of: ColorOf, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._color_of = color_of
        self._segments: list[tuple[str, int]] = []
        self._reveal = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(REVEAL_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._theme_refresh = ThemeRefreshBinding(self, lambda *_args, **_kwargs: self.update())

    def segments(self) -> list[tuple[str, int]]:
        return list(self._segments)

    def set_segments(self, segments, *, animate: bool = True) -> None:
        """``segments`` — пары (ключ, число). Пустые доли не рисуются."""
        self._segments = [(str(key), int(count)) for key, count in segments if int(count) > 0]
        self._anim.stop()
        if animate and self._segments and are_live_animations_enabled():
            self._reveal = 0.0
            self._anim.start()
        else:
            self._reveal = 1.0
        self.update()

    def _on_value(self, value) -> None:
        self._reveal = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setBrush(QColor(0, 0, 0, 22) if _is_light() else QColor(255, 255, 255, 20))
        painter.drawRoundedRect(rect, radius, radius)
        total = sum(count for _key, count in self._segments)
        if total:
            usable = max(0.0, rect.width() - self.GAP * (len(self._segments) - 1))
            # Всё, что правее границы заполнения, не рисуется: доли «выезжают» слева.
            painter.setClipRect(QRectF(rect.left(), rect.top(), rect.width() * self._reveal, rect.height()))
            x = rect.left()
            for key, count in self._segments:
                width = usable * count / total
                painter.setBrush(QColor(self._color_of(key)))
                painter.drawRoundedRect(QRectF(x, rect.top(), width, rect.height()), radius, radius)
                x += width + self.GAP
        painter.end()


__all__ = ["REVEAL_MS", "ShareBar"]
