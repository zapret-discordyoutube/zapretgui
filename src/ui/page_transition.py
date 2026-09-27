"""Переход между страницами: новая страница проявляется и раскрывается сверху вниз.

Вместо стандартного «выезда» qfluentwidgets по странице сверху вниз проходит
мягкая граница прозрачности: верх страницы виден сразу, нижние части
проявляются следом, а сама страница чуть подплывает на своё место.

Прозрачность делает QGraphicsOpacityEffect с маской-градиентом: Qt один раз
рисует страницу в картинку и дальше только смешивает её, поэтому переход
дешёвый. По окончании эффект снимается, в покое у страницы нет ничего лишнего.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QPoint, QPointF, Qt, QVariantAnimation
from PyQt6.QtGui import QBrush, QColor, QGradient, QLinearGradient
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

from ui.animation_policy import are_live_animations_enabled


PAGE_REVEAL_MS = 420
# Ширина мягкой границы раскрытия — доля высоты страницы.
REVEAL_SOFT_EDGE = 0.35
# На сколько пикселей страница подплывает снизу.
PAGE_DRIFT_PX = 10

_TRANSITION_ATTR = "_zapret_page_transition"


def _reveal_mask(progress: float) -> QBrush:
    """Градиент: выше границы страница видна, ниже — ещё прозрачна."""
    edge = -REVEAL_SOFT_EDGE + (1.0 + REVEAL_SOFT_EDGE) * progress
    gradient = QLinearGradient(QPointF(0.0, 0.0), QPointF(0.0, 1.0))
    gradient.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
    top = max(0.0, min(1.0, edge))
    bottom = max(0.0, min(1.0, edge + REVEAL_SOFT_EDGE))
    gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
    if top > 0.0:
        gradient.setColorAt(top, QColor(0, 0, 0, 255))
    if bottom > top:
        gradient.setColorAt(bottom, QColor(0, 0, 0, 0))
    if bottom < 1.0:
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
    return QBrush(gradient)


class _PageReveal:
    def __init__(self, page: QWidget) -> None:
        self.page = page
        self.base_pos = QPoint(page.pos())
        self.effect = QGraphicsOpacityEffect(page)
        self.effect.setOpacity(1.0)
        self.effect.setOpacityMask(_reveal_mask(0.0))
        page.setGraphicsEffect(self.effect)

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях WinUI общий fallback подменяет QPropertyAnimation.start.
        self.anim = QVariantAnimation(page)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setDuration(PAGE_REVEAL_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.anim.valueChanged.connect(self._on_value)
        self.anim.finished.connect(self.finish)

    def start(self) -> None:
        self._on_value(0.0)
        self.anim.start()

    def _on_value(self, value) -> None:
        if sip.isdeleted(self.page):
            return
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        self.effect.setOpacityMask(_reveal_mask(t))
        self.page.move(self.base_pos.x(), self.base_pos.y() + round(PAGE_DRIFT_PX * (1.0 - t)))

    def finish(self) -> None:
        if self.anim.state() != QVariantAnimation.State.Stopped:
            self.anim.stop()
        if sip.isdeleted(self.page):
            return
        self.page.move(self.base_pos)
        if self.page.graphicsEffect() is self.effect:
            self.page.setGraphicsEffect(None)
        self.page.__dict__.pop(_TRANSITION_ATTR, None)


def is_page_revealing(page: QWidget) -> bool:
    reveal = page.__dict__.get(_TRANSITION_ATTR)
    return reveal is not None and reveal.anim.state() != QVariantAnimation.State.Stopped


def finish_page_reveal(page: QWidget | None) -> None:
    """Сразу доводит переход до конца (например, при быстром переключении)."""
    if page is None or sip.isdeleted(page):
        return
    reveal = page.__dict__.get(_TRANSITION_ATTR)
    if reveal is not None:
        reveal.finish()


def reveal_page(page: QWidget | None, *, previous: QWidget | None = None) -> bool:
    """Запускает раскрытие только что показанной страницы. True — если пошло."""
    finish_page_reveal(previous)
    if page is None or sip.isdeleted(page):
        return False
    finish_page_reveal(page)
    if not are_live_animations_enabled() or not page.isVisible():
        return False
    window = page.window()
    if window is None or window.isMinimized():
        return False
    if page.graphicsEffect() is not None:
        # У страницы уже есть свой эффект — не подменяем его.
        return False
    reveal = _PageReveal(page)
    page.__dict__[_TRANSITION_ATTR] = reveal
    reveal.start()
    return True


__all__ = ["finish_page_reveal", "is_page_revealing", "reveal_page"]
