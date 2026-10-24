"""Прокрутка списка без перерисовки всех плиток на каждом шаге.

Список «Мои пресеты» прозрачный: сквозь него виден фон окна. Поэтому Qt не
сдвигает уже нарисованные точки, а перерисовывает всю видимую часть на каждом
шаге прокрутки (десятки плиток на Python). Если сказать Qt, что виджет красит
все свои точки сам (WA_OpaquePaintEvent), он сдвигает готовую картинку и просит
дорисовать только открывшуюся полоску, но тогда фон под плитками обязан
положить сам виджет, иначе в полоске останутся старые плитки.

Этим занимается помощник, и только когда это безопасно:

- только на время прокрутки (пачка шагов подряд); когда она закончилась,
  виджет снова прозрачный и один раз перерисовывается обычным путём;
- только если фон за списком одноцветный. Это не предположение, а замер:
  Qt рисует окно (без самого списка) и читаются точки за ним; разные точки
  (картинка на фоне окна) — прозрачный путь остаётся как был;
- не во время другой анимации поверх списка (бегунок активного пресета).

Фон кладётся «как есть» (режим Source): так же, как Qt стирает область перед
перерисовкой, поэтому полоса не отличается от обычной отрисовки.
"""

from __future__ import annotations

import time
from typing import Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QImage, QPainter, QRegion
from PyQt6.QtWidgets import QWidget


# Сколько тишины считается концом прокрутки.
SCROLL_IDLE_MS = 140
# После неудачного замера (фон не одноцветный) заново не меряем столько секунд.
REMEASURE_AFTER_S = 1.0
_EDGE = 4


class OpaqueScroll(QObject):
    def __init__(self, view, *, is_busy: Callable[[], bool] | None = None) -> None:
        super().__init__(view)
        self._view = view
        self._is_busy = is_busy
        self._active = False
        self._measuring = False
        self._backdrop: QImage | None = None
        self._no_measure_until = 0.0
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(SCROLL_IDLE_MS)
        self._idle.timeout.connect(self.end)

    # ---- что спрашивает список -------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def painting_suppressed(self) -> bool:
        """Идёт замер фона: сам список ничего не рисует."""
        return self._measuring

    def on_scroll(self) -> None:
        """Вызывается перед каждым шагом прокрутки (до того, как Qt сдвинет картинку)."""
        if not self._active and not self._begin():
            return
        self._idle.start()

    def paint_backdrop(self, region: QRegion) -> None:
        """Кладёт фон под плитки в перерисовываемой области."""
        backdrop = self._backdrop
        if not self._active or backdrop is None:
            return
        painter = QPainter(self._view.viewport())
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(region.boundingRect(), QBrush(backdrop))
        painter.end()

    def end(self) -> None:
        """Прокрутка закончилась (или мешает другая анимация): вернуть обычный путь."""
        if not self._active:
            return
        self._idle.stop()
        self._active = False
        self._backdrop = None
        viewport = self._view.viewport()
        if sip.isdeleted(viewport):
            return
        viewport.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        # Последний кадр рисует Qt обычным путём: что бы ни накопилось, исчезнет.
        viewport.update()

    # ---- начало пачки ------------------------------------------------------

    def _begin(self) -> bool:
        view = self._view
        viewport = view.viewport()
        if time.monotonic() < self._no_measure_until:
            return False
        if not view.isVisible() or viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent):
            return False
        window = view.window()
        if window is None or window.isMinimized():
            return False
        if self._is_busy is not None and self._is_busy():
            return False
        try:
            backdrop = self._measure_flat_backdrop(window, viewport)
        except Exception:
            backdrop = None
        if backdrop is None:
            self._no_measure_until = time.monotonic() + REMEASURE_AFTER_S
            return False
        self._backdrop = backdrop
        self._active = True
        viewport.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        return True

    def _measure_flat_backdrop(self, window: QWidget, viewport: QWidget) -> QImage | None:
        """Точка фона, если за списком везде один и тот же цвет; иначе None."""
        width, height = viewport.width(), viewport.height()
        if width < 3 * _EDGE or height < 3 * _EDGE:
            return None
        points = (
            QPoint(_EDGE, _EDGE),
            QPoint(width - _EDGE, _EDGE),
            QPoint(_EDGE, height - _EDGE),
            QPoint(width - _EDGE, height - _EDGE),
            QPoint(width // 2, height // 2),
            QPoint(width // 3, height // 5),
        )
        samples: list[bytes] = []
        first: QImage | None = None
        flags = QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren
        self._measuring = True
        try:
            for point in points:
                image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(0)
                painter = QPainter(image)
                source = viewport.mapTo(window, point) if window is not viewport else point
                # Левый верхний угол области ложится в targetOffset, поэтому (0, 0).
                window.render(painter, QPoint(0, 0), QRegion(QRect(source, QSize(1, 1))), flags)
                painter.end()
                samples.append(bytes(image.constBits().asarray(4)))
                if first is None:
                    first = image
        finally:
            self._measuring = False
        if first is None or len(set(samples)) != 1:
            return None
        return first
