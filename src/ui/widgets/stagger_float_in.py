"""Карточки выплывают снизу вверх по очереди, когда контейнер показывают.

Помощник вешается на контейнер: содержимое каждой страницы (BasePage) и
вкладки «О программе». При каждом показе он проходит по виджетам его
раскладки сверху вниз и каждому ненадолго ставит эффект, который рисует
виджет полупрозрачным и чуть ниже своего места; эффект плавно доводит его
до места и снимается. Раскладку эффект не трогает, поэтому соседи не прыгают.

Выплывают только карточки, которые сейчас видны на экране.

Появление есть у каждой страницы, выключать его нельзя. Если виджет большой
и рисуется вручную (например, сетка плиток hosts) и эффект обходился бы
дорого, ему дают метод ``play_float_in(delay_ms)``: помощник вызывает его
в общей очереди вместо эффекта, а при скрытии — ``finish_float_in()``.
Такой вход рисуется теми же константами и той же плавностью
(``float_in_progress``).

``skip_float_in`` — только для виджетов, у которых вход уже есть свой
(вкладки «О программе» со своим помощником, девиз, шапка с глобусом);
архитектурная проверка не пускает его в другие файлы.
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
# Метод «своего входа» у виджета: play_float_in(delay_ms) / finish_float_in().
OWN_FLOAT_IN_METHOD = "play_float_in"
OWN_FLOAT_IN_FINISH = "finish_float_in"


def float_in_progress(elapsed_ms: float) -> float:
    """Доля пройденного пути выплывания (0..1) с той же плавностью OutCubic."""
    linear = max(0.0, min(1.0, float(elapsed_ms) / FLOAT_IN_DURATION_MS))
    return 1.0 - (1.0 - linear) ** 3


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
    """Подключается к контейнеру и оживляет его при каждом показе."""

    def __init__(self, container: QWidget) -> None:
        super().__init__(container)
        self._container = container
        self._running: list[_FloatIn] = []
        # Виджеты со своим входом, запущенные в этот показ.
        self._own: list[QWidget] = []
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
        return any(not sip.isdeleted(item) for item in self._running) or bool(self._own)

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
            if widget.graphicsEffect() is not None and not _has_own_float_in(widget):
                # Чужой эффект (тень и т.п.) не подменяем.
                continue
            if widget.visibleRegion().isEmpty():
                # Ниже края окна: выплывать там некому смотреть.
                continue
            result.append(widget)
        return result

    def play(self) -> None:
        if sip.isdeleted(self._container) or not self._container.isVisible():
            return
        if not are_live_animations_enabled():
            return
        window = self._container.window()
        if window is not None and window.isMinimized():
            return
        self.finish_all()
        for order, widget in enumerate(self._targets()):
            delay = min(order, _MAX_STAGGERED) * FLOAT_IN_STEP_MS
            if _has_own_float_in(widget):
                getattr(widget, OWN_FLOAT_IN_METHOD)(delay)
                self._own.append(widget)
            else:
                self._running.append(_FloatIn(widget, delay, self))

    def finish_all(self) -> None:
        running, self._running = self._running, []
        for item in running:
            if not sip.isdeleted(item):
                item.finish()
        own, self._own = self._own, []
        for widget in own:
            finish = getattr(widget, OWN_FLOAT_IN_FINISH, None)
            if not sip.isdeleted(widget) and callable(finish):
                finish()


def _has_own_float_in(widget: QWidget) -> bool:
    return callable(getattr(widget, OWN_FLOAT_IN_METHOD, None))


def attach_stagger_float_in(container: QWidget) -> StaggeredFloatIn:
    """Подключает выплывание карточек к контейнеру (один раз на контейнер)."""
    controller = container.__dict__.get(_CONTROLLER_ATTR)
    if controller is None:
        controller = StaggeredFloatIn(container)
        container.__dict__[_CONTROLLER_ATTR] = controller
    return controller


def stagger_float_in(container: QWidget) -> StaggeredFloatIn | None:
    try:
        return container.__dict__.get(_CONTROLLER_ATTR)
    except Exception:
        return None


def float_in(widget: QWidget, *, delay_ms: int = 0) -> bool:
    """Один виджет выплывает снизу (новая строка результата и т.п.).

    Возвращает False, если выплывания не будет: анимации выключены, окно
    свёрнуто или у виджета уже есть свой эффект.
    """
    if sip.isdeleted(widget) or not are_live_animations_enabled():
        return False
    if widget.graphicsEffect() is not None:
        return False
    window = widget.window()
    if window is not None and window.isMinimized():
        return False
    _FloatIn(widget, delay_ms, widget)
    return True


def skip_float_in(widget: QWidget) -> QWidget:
    """Помечает виджет, у которого вход уже есть свой (например, девиз).

    Не для того, чтобы просто выключить появление: большой виджет с ручной
    отрисовкой получает ``play_float_in``. Разрешённые файлы — в
    ``app.architecture_checks.check_skip_float_in_is_allowlisted``.
    """
    widget.__dict__[NO_FLOAT_IN_ATTR] = True
    return widget


__all__ = [
    "FLOAT_IN_DURATION_MS",
    "FLOAT_IN_RISE_PX",
    "FLOAT_IN_STEP_MS",
    "StaggeredFloatIn",
    "attach_stagger_float_in",
    "float_in",
    "float_in_progress",
    "skip_float_in",
    "stagger_float_in",
]
