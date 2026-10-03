"""Свой набор значков для главной страницы, нарисованный кодом.

Раньше здесь стояли значки из шрифта FontAwesome: сплошные, разной
«плотности», на мелком размере они расплывались. Эти нарисованы линиями
одной толщины со скруглёнными концами и мягкой полупрозрачной заливкой того
же цвета, поэтому смотрятся одним набором и остаются чёткими на любом
масштабе экрана.

Каждый значок описан в квадрате 24×24: функция рисует его линиями и
заливкой, а ``line_icon_pixmap`` один раз превращает это в картинку нужного
размера и цвета и дальше отдаёт её из кэша.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

from ui.widgets.star_glyph import build_star_path


GRID = 24.0
# Толщина линии в сетке 24×24: на настоящем размере 20–24 px это около двух пикселей.
STROKE = 2.0
FILL_ALPHA = 0.22
_CACHE_MAX = 96
_CACHE: OrderedDict[tuple[str, str, int, float], QPixmap] = OrderedDict()


def _folder(painter: QPainter, fill: QColor) -> None:
    # Папка одним контуром: язычок слева сверху и корпус.
    body = QPainterPath()
    body.moveTo(3.0, 7.0)
    body.quadTo(3.0, 5.0, 5.0, 5.0)
    body.lineTo(8.7, 5.0)
    body.quadTo(9.5, 5.0, 10.0, 5.6)
    body.lineTo(11.4, 7.3)
    body.lineTo(19.0, 7.3)
    body.quadTo(21.0, 7.3, 21.0, 9.3)
    body.lineTo(21.0, 17.0)
    body.quadTo(21.0, 19.0, 19.0, 19.0)
    body.lineTo(5.0, 19.0)
    body.quadTo(3.0, 19.0, 3.0, 17.0)
    body.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(body)
    # Полоска-ярлычок: пресет — это подписанная папка с настройками.
    painter.drawLine(QPointF(7.0, 14.6), QPointF(13.0, 14.6))


def _folder_open(painter: QPainter, fill: QColor) -> None:
    # Открытая папка: задняя стенка и наклонённая вперёд передняя.
    back = QPainterPath()
    back.moveTo(3.4, 17.2)
    back.lineTo(3.4, 6.8)
    back.quadTo(3.4, 5.0, 5.2, 5.0)
    back.lineTo(8.8, 5.0)
    back.quadTo(9.6, 5.0, 10.1, 5.6)
    back.lineTo(11.5, 7.2)
    back.lineTo(17.6, 7.2)
    back.quadTo(19.4, 7.2, 19.4, 9.0)
    back.lineTo(19.4, 10.4)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(back)
    front = QPainterPath()
    front.moveTo(6.6, 10.4)
    front.lineTo(20.4, 10.4)
    front.quadTo(22.0, 10.4, 21.5, 11.9)
    front.lineTo(19.6, 17.7)
    front.quadTo(19.2, 19.0, 17.8, 19.0)
    front.lineTo(4.6, 19.0)
    front.quadTo(3.0, 19.0, 3.5, 17.5)
    front.lineTo(5.3, 11.6)
    front.quadTo(5.7, 10.4, 6.6, 10.4)
    front.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(front)


def _profiles(painter: QPainter, fill: QColor) -> None:
    # Список профилей: три строки, у каждой свой маркер.
    for row, right in ((6.0, 20.5), (12.0, 17.5), (18.0, 20.5)):
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(3.2, row - 2.1, 4.2, 4.2), 1.3, 1.3)
        painter.drawLine(QPointF(11.0, row), QPointF(right, row))


def _shield(painter: QPainter, fill: QColor) -> None:
    # Щит с галочкой: режим, который сейчас защищает соединения.
    shield = QPainterPath()
    shield.moveTo(12.0, 3.2)
    shield.lineTo(18.6, 5.6)
    shield.quadTo(19.4, 5.9, 19.4, 6.8)
    shield.lineTo(19.4, 11.2)
    shield.cubicTo(19.4, 15.6, 16.4, 19.2, 12.0, 20.9)
    shield.cubicTo(7.6, 19.2, 4.6, 15.6, 4.6, 11.2)
    shield.lineTo(4.6, 6.8)
    shield.quadTo(4.6, 5.9, 5.4, 5.6)
    shield.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(shield)
    check = QPainterPath()
    check.moveTo(8.7, 12.1)
    check.lineTo(11.0, 14.4)
    check.lineTo(15.4, 9.8)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(check)


def _star(painter: QPainter, fill: QColor) -> None:
    painter.setBrush(fill)
    painter.drawPath(build_star_path(QPointF(12.0, 12.6), 9.4))


def _tour(painter: QPainter, fill: QColor) -> None:
    # Шапочка выпускника: ромб, тулья под ним и кисточка.
    crown = QPainterPath()
    crown.moveTo(6.6, 11.6)
    crown.lineTo(6.6, 15.6)
    crown.cubicTo(6.6, 17.2, 9.0, 18.6, 12.0, 18.6)
    crown.cubicTo(15.0, 18.6, 17.4, 17.2, 17.4, 15.6)
    crown.lineTo(17.4, 11.6)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(crown)
    cap = QPainterPath()
    cap.moveTo(12.0, 4.6)
    cap.lineTo(21.4, 9.2)
    cap.lineTo(12.0, 13.8)
    cap.lineTo(2.6, 9.2)
    cap.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(cap)
    painter.drawLine(QPointF(21.4, 9.2), QPointF(21.4, 14.4))


def _wifi(painter: QPainter, fill: QColor) -> None:
    # Три дуги сигнала и точка — проверка соединения.
    painter.setBrush(Qt.BrushStyle.NoBrush)
    center = QPointF(12.0, 18.4)
    for radius in (4.6, 8.6, 12.6):
        box = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        # Углы в 1/16 градуса: дуга в 90° с вершиной наверху.
        painter.drawArc(box, 45 * 16, 90 * 16)
    solid = QColor(painter.pen().color())
    painter.setBrush(solid)
    painter.drawEllipse(center, 1.1, 1.1)
    _ = fill


def _network_reset(painter: QPainter, fill: QColor) -> None:
    # Круговая стрелка вокруг узла сети: сброс сетевых настроек.
    painter.setBrush(fill)
    painter.drawEllipse(QPointF(12.0, 12.0), 3.0, 3.0)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    ring = QRectF(3.6, 3.6, 16.8, 16.8)
    painter.drawArc(ring, 35 * 16, 250 * 16)
    head = QPainterPath()
    head.moveTo(15.4, 4.2)
    head.lineTo(19.0, 6.9)
    head.lineTo(18.4, 2.6)
    painter.drawPath(head)


def _book(painter: QPainter, fill: QColor) -> None:
    # Раскрытая книга: две страницы и корешок.
    pages = QPainterPath()
    pages.moveTo(12.0, 6.8)
    pages.cubicTo(9.8, 5.2, 7.0, 4.8, 3.6, 5.0)
    pages.lineTo(3.6, 17.6)
    pages.cubicTo(7.0, 17.4, 9.8, 17.8, 12.0, 19.4)
    pages.cubicTo(14.2, 17.8, 17.0, 17.4, 20.4, 17.6)
    pages.lineTo(20.4, 5.0)
    pages.cubicTo(17.0, 4.8, 14.2, 5.2, 12.0, 6.8)
    pages.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(pages)
    painter.drawLine(QPointF(12.0, 6.8), QPointF(12.0, 19.4))


_ICONS: dict[str, Callable[[QPainter, QColor], None]] = {
    "preset": _folder,
    "profiles": _profiles,
    "mode": _shield,
    "star": _star,
    "tour": _tour,
    "connection_test": _wifi,
    "network_reset": _network_reset,
    "folder": _folder_open,
    "docs": _book,
}


def line_icon_names() -> tuple[str, ...]:
    return tuple(_ICONS)


def paint_line_icon(painter: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Рисует значок ``name`` в прямоугольник ``rect`` цветом ``color``."""
    draw = _ICONS.get(name)
    if draw is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.left(), rect.top())
    painter.scale(rect.width() / GRID, rect.height() / GRID)
    pen = QPen(QColor(color))
    pen.setWidthF(STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    fill = QColor(color)
    fill.setAlphaF(FILL_ALPHA)
    draw(painter, fill)
    painter.restore()


def line_icon_pixmap(name: str, *, color: str, size: int = 24, ratio: float = 1.0) -> QPixmap:
    """Готовая картинка значка; повторные запросы берутся из кэша."""
    ratio = max(1.0, float(ratio or 1.0))
    key = (str(name), QColor(color).name(), int(size), ratio)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached
    side = max(1, round(size * ratio))
    pixmap = QPixmap(side, side)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_line_icon(painter, name, QRectF(0.0, 0.0, float(size), float(size)), QColor(color))
    painter.end()
    _CACHE[key] = pixmap
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return pixmap


__all__ = ["line_icon_names", "line_icon_pixmap", "paint_line_icon"]
