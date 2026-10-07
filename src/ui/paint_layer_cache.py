"""Готовые слои для живых анимаций.

В кадре анимации почти всё неподвижно: у сцены обхода это пол, глобус,
дорожки и стена, у медоеда — тело и голова, у карточки — рамка и подкраска.
Двигаются пакеты, дуга и лапа. Но Qt перерисовывает область целиком, и
неподвижное рисовалось заново 20–30 раз в секунду: контуры со сглаживанием,
градиенты, обводки.

Здесь неподвижная часть рисуется один раз в картинку (слой), а в кадре
накладывается готовой. Картинка получается той же самой, что и при прямом
рисовании, потому что слой рисуется сразу в точках экрана:

- с тем же масштабом экрана (125 %, 150 %…) и тем же поворотом;
- с тем же дробным сдвигом. При масштабе 125 % или 150 % виджет начинается
  не с целой точки экрана, и сглаженные края зависят от этой доли точки.
  Слой, нарисованный «с нуля», дал бы края, сдвинутые на долю точки.

Накладывается слой точка в точку, без растяжения. От прямого рисования он
может отличаться только округлением при смешивании цветов — не больше чем
на две единицы из 255 на сглаженных краях и полупрозрачных местах (там, где
несколько слоёв лежат друг на друге, — до трёх в отдельных точках). Глазом
это не видно; проверяют tests/test_paint_layer_cache.py и тесты виджетов.

Слой подходит только для того, что между кадрами не меняется. Всё, от чего
зависит вид слоя (цвет, размер, тема), вызывающий код кладёт в ключ.

Сам вызов ``draw`` должен быть дешёвым: он выполняется на каждом кадре, а в
PyQt каждое обращение к Qt стоит заметно дороже обычной строки Python. Поэтому
для того же места на экране всё посчитанное (картинка и куда её класть)
запоминается, и повторный кадр — это несколько обращений к Qt и одно наложение.

Замер в собранной программе на Windows (главная страница, обход работает,
около 155 наложений в секунду): на эти части кадра уходило около 600 мс за
30 секунд, со слоями — около 95 мс.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable, Hashable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPixmap, QTransform


_SHIFT_ONLY = (QTransform.TransformationType.TxNone, QTransform.TransformationType.TxTranslate)
_SOURCE_OVER = QPainter.CompositionMode.CompositionMode_SourceOver
# Слой больше этого (в точках экрана) в память не кладём, рисуем как раньше.
MAX_LAYER_PIXELS = 4_000_000


class LayerCache:
    """Небольшой запас готовых слоёв: редко нужные вытесняются."""

    def __init__(self, capacity: int = 16) -> None:
        self._capacity = max(1, int(capacity))
        # Картинки слоёв. Ключ — вид слоя, масштаб, поворот и доля точки экрана:
        # сдвиг на целое число точек (прокрутка страницы) картинку не меняет.
        self._layers: OrderedDict[tuple, QPixmap] = OrderedDict()
        # Уже посчитанные наложения для точного места на экране:
        # место → (ключ картинки, картинка, куда класть, откуда брать).
        self._placed: OrderedDict[tuple, tuple[tuple, QPixmap, QRectF, QRectF]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._layers)

    def clear(self) -> None:
        self._layers.clear()
        self._placed.clear()

    def draw(
        self,
        painter: QPainter,
        key: Hashable,
        rect: QRectF,
        paint: Callable[[QPainter], None],
    ) -> bool:
        """Рисует слой ``paint`` на ``painter``. True — слой взят готовым или только что запомнен.

        ``rect`` — область слоя в координатах ``painter``: за её пределами
        ``paint`` ничего не рисует. ``key`` — всё, от чего зависит вид слоя.
        Перо и кисть ``painter`` после вызова те же, что были до него.
        """
        to_device = painter.deviceTransform()
        if not to_device.isAffine() or painter.opacity() < 1.0 or painter.compositionMode() != _SOURCE_OVER:
            return self._paint_directly(painter, paint)
        # Куда на экране попадают начало координат и единичные шаги по осям:
        # это и сдвиг, и масштаб, и поворот — тремя обращениями к Qt.
        dx, dy = to_device.map(0.0, 0.0)
        ax, ay = to_device.map(1.0, 0.0)
        bx, by = to_device.map(0.0, 1.0)
        place = (key, dx, dy, ax, ay, bx, by, *rect.getRect())
        placed = self._placed.get(place)
        if placed is not None:
            # Тот же слой на том же месте, что и в прошлом кадре.
            self._placed.move_to_end(place)
            self._layers.move_to_end(placed[0])
            painter.drawPixmap(placed[2], placed[1], placed[3])
            return True

        if ay == dy and bx == dx and ax > dx and by > dy:
            return self._draw_upright(painter, key, rect, paint, to_device, place)
        return self._draw_turned(painter, key, rect, paint, to_device)

    # ---- без поворота: обычный случай ------------------------------------

    def _draw_upright(self, painter, key, rect, paint, to_device, place) -> bool:
        scale_x, scale_y = to_device.m11(), to_device.m22()
        shift_x, shift_y = to_device.dx(), to_device.dy()
        x, y, w, h = rect.getRect()
        left, top = math.floor(scale_x * x + shift_x), math.floor(scale_y * y + shift_y)
        width = math.ceil(scale_x * (x + w) + shift_x) - left
        height = math.ceil(scale_y * (y + h) + shift_y) - top
        if width <= 0 or height <= 0 or width * height > MAX_LAYER_PIXELS:
            return self._paint_directly(painter, paint)

        layer_key = (
            key, width, height,
            round(scale_x, 6), 0.0, 0.0, round(scale_y, 6),
            round(shift_x - left, 3), round(shift_y - top, 3),
        )
        layer = self._layer(
            painter, layer_key, width, height, paint,
            QTransform(scale_x, 0.0, 0.0, scale_y, shift_x - left, shift_y - top),
        )
        # Область в координатах painter, которая на экране даёт ровно точки
        # слоя: Qt кладёт картинку точка в точку, без растяжения.
        target = QRectF((left - shift_x) / scale_x, (top - shift_y) / scale_y, width / scale_x, height / scale_y)
        source = QRectF(0.0, 0.0, width, height)
        if layer_key in self._layers:
            self._placed[place] = (layer_key, layer, target, source)
            while len(self._placed) > self._capacity:
                self._placed.popitem(last=False)
        painter.drawPixmap(target, layer, source)
        return True

    # ---- с поворотом: редкий случай (наклонённый медоед) -----------------

    def _draw_turned(self, painter, key, rect, paint, to_device) -> bool:
        world, invertible = painter.worldTransform().inverted()
        if not invertible:
            return self._paint_directly(painter, paint)
        bounds = to_device.mapRect(QRectF(rect))
        left, top = math.floor(bounds.left()), math.floor(bounds.top())
        width, height = math.ceil(bounds.right()) - left, math.ceil(bounds.bottom()) - top
        if width <= 0 or height <= 0 or width * height > MAX_LAYER_PIXELS:
            return self._paint_directly(painter, paint)

        layer_transform = QTransform(
            to_device.m11(), to_device.m12(),
            to_device.m21(), to_device.m22(),
            to_device.dx() - left, to_device.dy() - top,
        )
        layer_key = (
            key, width, height,
            round(layer_transform.m11(), 6), round(layer_transform.m12(), 6),
            round(layer_transform.m21(), 6), round(layer_transform.m22(), 6),
            round(layer_transform.dx(), 3), round(layer_transform.dy(), 3),
        )
        layer = self._layer(painter, layer_key, width, height, paint, layer_transform)

        # Накладываем точка в точку: убираем своё преобразование и то, что
        # добавляет Qt (сдвиг виджета в окне, масштаб экрана).
        base = world * to_device
        painter.save()
        if base.type() in _SHIFT_ONLY:
            painter.setWorldTransform(QTransform.fromTranslate(-base.dx(), -base.dy()))
        else:
            painter.setWorldTransform(base.inverted()[0])
        painter.drawPixmap(QPointF(left, top), layer)
        painter.restore()
        return True

    # ---- общее -----------------------------------------------------------

    def _layer(self, painter, layer_key, width, height, paint, layer_transform) -> QPixmap:
        layer = self._layers.get(layer_key)
        if layer is not None:
            self._layers.move_to_end(layer_key)
            return layer
        layer = QPixmap(width, height)
        layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        layer_painter.setRenderHints(painter.renderHints())
        layer_painter.setPen(painter.pen())
        layer_painter.setBrush(painter.brush())
        layer_painter.setFont(painter.font())
        # Тот же масштаб, поворот и та же доля точки экрана, что у виджета.
        layer_painter.setTransform(layer_transform)
        try:
            paint(layer_painter)
        finally:
            layer_painter.end()
        self._layers[layer_key] = layer
        while len(self._layers) > self._capacity:
            dropped, _pixmap = self._layers.popitem(last=False)
            for place in [place for place, placed in self._placed.items() if placed[0] == dropped]:
                del self._placed[place]
        return layer

    @staticmethod
    def _paint_directly(painter: QPainter, paint: Callable[[QPainter], None]) -> bool:
        painter.save()
        try:
            paint(painter)
        finally:
            painter.restore()
        return False


__all__ = ["MAX_LAYER_PIXELS", "LayerCache"]
