"""Переезд активной строки в списке: акцентная полоска скользит к новой строке.

Помощник подключается к готовому списку (QListView/QListWidget) и следит за
ролью «активна» в модели. Когда активной становится другая строка, поверх
списка по прозрачному слою едет акцентная полоска — «гусеницей»: передний
край убегает вперёд, задний догоняет. За ней скользит мягкая подсветка
строки, а после приземления значок новой строки подпрыгивает.

При полной перестройке списка (фильтр, обновление) ничего не анимируется.
В покое помощник ничего не рисует и таймеров не держит.
"""

from __future__ import annotations

import math

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPersistentModelIndex, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled


SLIDE_DURATION_MS = 460
BOUNCE_DURATION_MS = 420
# Доля общего времени, отведённая на переезд; остальное — прыжок значка.
_SLIDE_SHARE = SLIDE_DURATION_MS / (SLIDE_DURATION_MS + BOUNCE_DURATION_MS)
_MOTION_ATTR = "_zapret_active_row_motion"


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class _MotionOverlay(QWidget):
    """Прозрачный слой над строками списка; мышь проходит насквозь."""

    def __init__(self, motion: "ActiveRowMotion", parent: QWidget) -> None:
        super().__init__(parent)
        self._motion = motion
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        self._motion.paint_overlay(self)


class ActiveRowMotion(QObject):
    def __init__(self, view, active_role: int, *, marker_inset: int = 6, row_rect_fn=None) -> None:
        super().__init__(view)
        self._view = view
        self._active_role = int(active_role)
        self._marker_inset = int(marker_inset)
        self._row_rect_fn = row_rect_fn
        self._active = QPersistentModelIndex()
        self._landing = QPersistentModelIndex()
        self._from_rect = QRect()
        self._t = 0.0
        self._check_scheduled = False
        self._model = None

        self._overlay = _MotionOverlay(self, view.viewport())
        view.viewport().installEventFilter(self)

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(SLIDE_DURATION_MS + BOUNCE_DURATION_MS)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)

        self._bind_model(view.model())

    # ---- модель --------------------------------------------------------

    def _bind_model(self, model) -> None:
        if model is self._model:
            return
        if self._model is not None:
            for signal, slot in self._model_connections():
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
        self._model = model
        if model is None:
            return
        for signal, slot in self._model_connections():
            signal.connect(slot)
        self._rescan_without_animation()

    def _model_connections(self):
        model = self._model
        return (
            (model.dataChanged, self._on_data_changed),
            (model.modelReset, self._rescan_without_animation),
            (model.rowsInserted, self._on_rows_changed),
            (model.rowsRemoved, self._on_rows_changed),
            (model.layoutChanged, self._rescan_without_animation),
        )

    def _find_active(self) -> QPersistentModelIndex:
        model = self._model
        if model is None:
            return QPersistentModelIndex()
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if bool(index.data(self._active_role)):
                return QPersistentModelIndex(index)
        return QPersistentModelIndex()

    def _rescan_without_animation(self, *args) -> None:
        _ = args
        if not self._alive():
            return
        self._stop()
        self._active = self._find_active()

    def _on_rows_changed(self, *args) -> None:
        _ = args
        if not self._alive():
            return
        # Строки добавляются по одной при перестройке списка — пересчитываем
        # один раз в конце, без анимации.
        self._stop()
        if not self._check_scheduled:
            self._check_scheduled = True
            QTimer.singleShot(0, self._rescan_after_rows_changed)

    def _rescan_after_rows_changed(self) -> None:
        self._check_scheduled = False
        if not self._alive():
            return
        self._active = self._find_active()

    def _on_data_changed(self, top_left, bottom_right, roles=()) -> None:
        _ = (top_left, bottom_right)
        if roles and self._active_role not in [int(role) for role in roles]:
            return
        if not self._check_scheduled:
            # Старая строка гаснет и новая загорается двумя сигналами подряд —
            # смотрим на итог один раз.
            self._check_scheduled = True
            QTimer.singleShot(0, self._check_active_change)

    def _check_active_change(self) -> None:
        self._check_scheduled = False
        if not self._alive():
            return
        previous = self._active
        current = self._find_active()
        self._active = current
        if not previous.isValid() or not current.isValid() or previous == current:
            return
        self._start(previous, current)

    # ---- анимация ------------------------------------------------------

    def _row_rect(self, index) -> QRect:
        rect = self._view.visualRect(self._model.index(index.row(), 0))
        if self._row_rect_fn is not None:
            rect = self._row_rect_fn(rect)
        return rect

    def _start(self, previous: QPersistentModelIndex, current: QPersistentModelIndex) -> None:
        if not are_live_animations_enabled() or not self._view.isVisible():
            return
        window = self._view.window()
        if window is not None and window.isMinimized():
            return
        self._from_rect = self._row_rect(previous)
        self._landing = current
        self._overlay.setGeometry(self._view.viewport().rect())
        self._overlay.show()
        self._overlay.raise_()
        self._anim.stop()
        self._t = 0.0
        self._anim.start()

    def _stop(self) -> None:
        if self._anim.state() != QVariantAnimation.State.Stopped:
            self._anim.stop()
        self._on_finished()

    def _on_value(self, value) -> None:
        try:
            self._t = float(value)
        except (TypeError, ValueError):
            return
        self._overlay.update()
        if self._landing.isValid():
            self._view.viewport().update(self._row_rect(self._landing).adjusted(0, -8, 0, 8))

    def _alive(self) -> bool:
        return not (sip.isdeleted(self._overlay) or sip.isdeleted(self._view))

    def _on_finished(self) -> None:
        if not self._alive():
            # Список уже разбирается при закрытии, а модель ещё шлёт сигналы.
            return
        landing = self._landing
        self._t = 0.0
        self._landing = QPersistentModelIndex()
        self._overlay.hide()
        if landing.isValid() and self._model is not None:
            self._view.viewport().update(self._row_rect(landing).adjusted(0, -8, 0, 8))

    def is_running(self) -> bool:
        return self._anim.state() != QVariantAnimation.State.Stopped

    # ---- то, что спрашивает delegate -----------------------------------

    def _is_landing(self, index) -> bool:
        return (
            self._landing.isValid()
            and index is not None
            and index.isValid()
            and index.row() == self._landing.row()
            and self.is_running()
        )

    def hides_static_marker(self, index) -> bool:
        """Пока полоска едет, у новой строки своя полоска не рисуется."""
        return self._is_landing(index) and self._t < _SLIDE_SHARE

    def icon_offset(self, index) -> float:
        """Сдвиг значка новой строки по вертикали: прыжок после приземления."""
        if not self._is_landing(index) or self._t < _SLIDE_SHARE:
            return 0.0
        t = (self._t - _SLIDE_SHARE) / (1.0 - _SLIDE_SHARE)
        return -4.0 * math.sin(math.pi * min(1.0, t / 0.6)) if t < 0.6 else 1.2 * math.sin(math.pi * (t - 0.6) / 0.4)

    # ---- отрисовка слоя ------------------------------------------------

    def paint_overlay(self, overlay: QWidget) -> None:
        if not self._landing.isValid() or self._t >= _SLIDE_SHARE:
            return
        from ui.theme import get_theme_tokens, to_qcolor

        tokens = get_theme_tokens()
        accent = to_qcolor(tokens.accent_hex, "#5caee8")
        target = self._row_rect(self._landing)
        source = self._from_rect
        t = self._t / _SLIDE_SHARE
        moving_down = target.top() >= source.top()

        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Мягкая подсветка строки скользит следом и тает к приземлению.
        eased = _ease_in_out_cubic(t)
        ghost = QRectF(
            _lerp(source.left(), target.left(), eased),
            _lerp(source.top(), target.top(), eased),
            _lerp(source.width(), target.width(), eased),
            _lerp(source.height(), target.height(), eased),
        )
        fill = QColor(accent)
        fill.setAlphaF(0.16 * (1.0 - t))
        painter.setBrush(fill)
        painter.drawRoundedRect(ghost, 10, 10)

        # Полоска едет «гусеницей»: передний край быстрее заднего.
        lead = _ease_out_cubic(min(1.0, t * 1.25))
        trail = _ease_in_out_cubic(t)
        inset = self._marker_inset
        if moving_down:
            top = _lerp(source.top() + inset, target.top() + inset, trail)
            bottom = _lerp(source.bottom() - inset, target.bottom() - inset, lead)
        else:
            top = _lerp(source.top() + inset, target.top() + inset, lead)
            bottom = _lerp(source.bottom() - inset, target.bottom() - inset, trail)
        left = _lerp(source.left(), target.left(), eased) + inset
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(left, top, 4.0, max(8.0, bottom - top)), 2, 2)
        painter.end()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize and watched is self._view.viewport():
            self._overlay.setGeometry(self._view.viewport().rect())
        return False


def attach_active_row_motion(view, active_role: int, **kwargs) -> ActiveRowMotion:
    """Подключает переезд активной строки к списку (один раз на список)."""
    motion = view.__dict__.get(_MOTION_ATTR)
    if motion is None:
        motion = ActiveRowMotion(view, active_role, **kwargs)
        view.__dict__[_MOTION_ATTR] = motion
    return motion


def active_row_motion(view) -> ActiveRowMotion | None:
    try:
        return view.__dict__.get(_MOTION_ATTR)
    except Exception:
        return None


__all__ = ["ActiveRowMotion", "active_row_motion", "attach_active_row_motion"]
