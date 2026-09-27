"""Мягкое появление и исчезновение виджетов вместо резкого show/hide.

Уходящий элемент сначала затухает и только потом прячется. Новый появляется
сразу после этого и плавно проявляется, поэтому старое и новое не
наслаиваются, а карточка меняет высоту один раз. В покое у виджета нет
никаких эффектов: прозрачность снимается сразу по окончании перехода.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, QVariantAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

from ui.animation_policy import are_live_animations_enabled


FADE_OUT_MS = 160
FADE_IN_MS = 220
# Новый элемент ждёт, пока соседний успеет затухнуть.
FADE_IN_DELAY_MS = FADE_OUT_MS

_STATE_ATTR = "_zapret_soft_visibility"


class _SoftVisibilityState:
    def __init__(self, widget, target: bool) -> None:
        self.target = target
        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях WinUI общий fallback подменяет QPropertyAnimation.start.
        self.anim = QVariantAnimation(widget)
        self.anim.valueChanged.connect(lambda value, w=widget: _on_opacity(w, value))
        self.anim.finished.connect(lambda w=widget: _on_finished(w))
        self.delay = QTimer(widget)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(lambda w=widget: _begin_fade_in(w))


def _state(widget) -> _SoftVisibilityState | None:
    return widget.__dict__.get(_STATE_ATTR)


def soft_visibility_target(widget) -> bool:
    """Какой видимость станет после перехода (во время затухания — уже False)."""
    state = _state(widget)
    if state is not None:
        return state.target
    return not widget.isHidden()


def _can_animate(widget) -> bool:
    if not are_live_animations_enabled():
        return False
    window = widget.window()
    if window is None or not window.isVisible() or window.isMinimized():
        return False
    parent = widget.parentWidget()
    return parent is None or parent.isVisible()


def _effect(widget) -> QGraphicsOpacityEffect:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)
    return effect


def _clear_effect(widget) -> None:
    if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)


def _on_opacity(widget, value) -> None:
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        try:
            effect.setOpacity(float(value))
        except (TypeError, ValueError):
            pass


def _on_finished(widget) -> None:
    state = _state(widget)
    if state is None:
        return
    if not state.target:
        widget.setVisible(False)
    _clear_effect(widget)


def _begin_fade_in(widget) -> None:
    state = _state(widget)
    if state is None or not state.target:
        return
    effect = _effect(widget)
    effect.setOpacity(0.0)
    widget.setVisible(True)
    state.anim.stop()
    state.anim.setStartValue(0.0)
    state.anim.setEndValue(1.0)
    state.anim.setDuration(FADE_IN_MS)
    state.anim.start()


def set_visible_softly(widget, visible: bool) -> bool:
    """Показывает или прячет виджет с затуханием. True — если цель изменилась."""
    target = bool(visible)
    if not isinstance(widget, QWidget):
        # Не настоящий виджет (например, заглушка в тесте) — обычное переключение.
        current = None
        for probe in (lambda: not bool(widget.isHidden()), lambda: bool(widget.isVisible())):
            try:
                current = probe()
                break
            except Exception:
                continue
        if current is not None and current == target:
            return False
        widget.setVisible(target)
        return True
    if soft_visibility_target(widget) == target:
        return False

    state = _state(widget)
    if state is None:
        state = _SoftVisibilityState(widget, target)
        widget.__dict__[_STATE_ATTR] = state
    state.target = target
    state.delay.stop()

    if not _can_animate(widget):
        state.anim.stop()
        _clear_effect(widget)
        widget.setVisible(target)
        return True

    if target:
        if widget.isHidden():
            state.delay.start(FADE_IN_DELAY_MS)
        else:
            # Передумали прятать во время затухания — возвращаем с текущей точки.
            effect = _effect(widget)
            state.anim.stop()
            state.anim.setStartValue(float(effect.opacity()))
            state.anim.setEndValue(1.0)
            state.anim.setDuration(FADE_IN_MS)
            state.anim.start()
        return True

    if widget.isHidden():
        state.anim.stop()
        _clear_effect(widget)
        return True
    effect = _effect(widget)
    state.anim.stop()
    state.anim.setStartValue(float(effect.opacity()))
    state.anim.setEndValue(0.0)
    state.anim.setDuration(FADE_OUT_MS)
    state.anim.start()
    return True


__all__ = ["set_visible_softly", "soft_visibility_target"]
