"""Карточки вкладки выплывают снизу вверх по очереди, когда вкладку показывают.

Помощник вешается на контейнер вкладки. При каждом показе он проходит по
виджетам его раскладки сверху вниз и каждому ненадолго ставит эффект,
который рисует виджет полупрозрачным и чуть ниже своего места; эффект
плавно доводит его до места и снимается. Раскладку эффект не трогает,
поэтому соседи не прыгают.

Если в этот момент идёт переход между страницами (ui/page_transition.py),
выплывание пропускается: вход страницы уже анимирован, двойная анимация
лишняя. Виджет с атрибутом ``_zapret_no_float_in`` (у него свой вход,
например девиз) не трогается.
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtWidgets import QGraphicsEffect, QWidget

from ui.animation_policy import are_live_animations_enabled


FLOAT_IN_DURATION_MS = 460
FLOAT_IN_STEP_MS = 70
FLOAT_IN_RISE_PX = 16.0
# Дальше этого номера карточки идут без дополнительной задержки.
_MAX_STAGGERED = 8
_CONTROLLER_ATTR = "_zapret_stagger_float_in"
NO_FLOAT_IN_ATTR = "_zapret_no_float_in"


class _RiseEffect(QGraphicsEffect):
    """Рисует виджет полупрозрачным и сдвинутым вниз на остаток пути."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._progress = 0.0

    def set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    def boundingRectFor(self, rect: QRectF) -> QRectF:  # noqa: N802
        return rect.adjusted(0.0, 0.0, 0.0, FLOAT_IN_RISE_PX)

    def draw(self, painter) -> None:
        progress = self._progress
        if progress <= 0.0:
            return
        pixmap, offset = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        painter.save()
        try:
            painter.setOpacity(progress)
            painter.drawPixmap(QPointF(offset) + QPointF(0.0, FLOAT_IN_RISE_PX * (1.0 - progress)), pixmap)
        finally:
            painter.restore()


class _FloatIn(QObject):
    """Выплывание одного виджета с задержкой."""

    def __init__(self, widget: QWidget, delay_ms: int, parent: QObject) -> None:
        super().__init__(parent)
        self.widget = widget
        self.effect = _RiseEffect(widget)
        widget.setGraphicsEffect(self.effect)

        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(FLOAT_IN_DURATION_MS)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._on_value)
        self.animation.finished.connect(self.finish)

        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.timeout.connect(self.animation.start)
        self.delay.start(max(0, int(delay_ms)))

    def _on_value(self, value) -> None:
        if not sip.isdeleted(self.effect):
            self.effect.set_progress(float(value))

    def finish(self) -> None:
        self.delay.stop()
        self.animation.stop()
        widget = self.widget
        if not sip.isdeleted(widget) and widget.graphicsEffect() is self.effect:
            widget.setGraphicsEffect(None)
        self.deleteLater()


class StaggeredFloatIn(QObject):
    """Подключается к контейнеру вкладки и оживляет его при каждом показе."""

    def __init__(self, container: QWidget, *, page: QWidget | None = None) -> None:
        super().__init__(container)
        self._container = container
        self._page = page
        self._running: list[_FloatIn] = []
        container.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._container:
            kind = event.type()
            if kind == QEvent.Type.Show:
                # Раскладка к этому моменту ещё может досчитываться.
                QTimer.singleShot(0, self.play)
            elif kind == QEvent.Type.Hide:
                self.finish_all()
        return False

    def is_running(self) -> bool:
        return any(not sip.isdeleted(item) for item in self._running)

    def _targets(self) -> list[QWidget]:
        layout = self._container.layout()
        if layout is None:
            return []
        result: list[QWidget] = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None or widget.isHidden():
                continue
            if widget.__dict__.get(NO_FLOAT_IN_ATTR):
                continue
            if widget.graphicsEffect() is not None:
                # Чужой эффект (тень и т.п.) не подменяем.
                continue
            result.append(widget)
        return result

    def _page_is_revealing(self) -> bool:
        if self._page is None:
            return False
        try:
            from ui.page_transition import is_page_revealing

            return bool(is_page_revealing(self._page))
        except Exception:
            return False

    def play(self) -> None:
        if sip.isdeleted(self._container) or not self._container.isVisible():
            return
        if not are_live_animations_enabled() or self._page_is_revealing():
            return
        window = self._container.window()
        if window is not None and window.isMinimized():
            return
        self.finish_all()
        for order, widget in enumerate(self._targets()):
            delay = min(order, _MAX_STAGGERED) * FLOAT_IN_STEP_MS
            self._running.append(_FloatIn(widget, delay, self))

    def finish_all(self) -> None:
        running, self._running = self._running, []
        for item in running:
            if not sip.isdeleted(item):
                item.finish()


def attach_stagger_float_in(container: QWidget, *, page: QWidget | None = None) -> StaggeredFloatIn:
    """Подключает выплывание карточек к контейнеру (один раз на контейнер)."""
    controller = container.__dict__.get(_CONTROLLER_ATTR)
    if controller is None:
        controller = StaggeredFloatIn(container, page=page)
        container.__dict__[_CONTROLLER_ATTR] = controller
    return controller


def stagger_float_in(container: QWidget) -> StaggeredFloatIn | None:
    try:
        return container.__dict__.get(_CONTROLLER_ATTR)
    except Exception:
        return None


def skip_float_in(widget: QWidget) -> QWidget:
    """Помечает виджет, у которого свой вход (например, девиз)."""
    widget.__dict__[NO_FLOAT_IN_ATTR] = True
    return widget


__all__ = [
    "StaggeredFloatIn",
    "attach_stagger_float_in",
    "skip_float_in",
    "stagger_float_in",
]
