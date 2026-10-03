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

import math
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


def _bump(t: float, start: float = 0.0, end: float = 1.0) -> float:
    """Мягкий горб 0→1→0 на отрезке [start, end] шкалы жеста, вне его — 0."""
    if t <= start or t >= end:
        return 0.0
    return math.sin(math.pi * (t - start) / (end - start))


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _folder(painter: QPainter, fill: QColor, t: float) -> None:
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
    # Полоска-ярлычок: пресет — подписанная папка. В жесте её «дописывают» заново.
    grow = 1.0 - _ease(t / 0.3) if t < 0.3 else _ease((t - 0.3) / 0.55)
    painter.drawLine(QPointF(7.0, 14.6), QPointF(7.0 + 6.0 * max(0.08, grow), 14.6))


def _folder_open(painter: QPainter, fill: QColor, t: float) -> None:
    # Открытая папка: задняя стенка и передняя, которая в жесте приоткрывается сильнее.
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
    lift = 2.2 * _bump(t)
    front = QPainterPath()
    front.moveTo(6.6, 10.4 + lift)
    front.lineTo(20.4, 10.4 + lift)
    front.quadTo(22.0, 10.4 + lift, 21.5 + 0.4 * lift, 11.9 + lift)
    front.lineTo(19.6, 17.7)
    front.quadTo(19.2, 19.0, 17.8, 19.0)
    front.lineTo(4.6, 19.0)
    front.quadTo(3.0, 19.0, 3.5, 17.5)
    front.lineTo(5.3 + 0.3 * lift, 11.6 + lift)
    front.quadTo(5.7, 10.4 + lift, 6.6, 10.4 + lift)
    front.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(front)


def _profiles(painter: QPainter, fill: QColor, t: float) -> None:
    # Список профилей: три строки, у каждой свой маркер. В жесте строки
    # по очереди «отмечаются»: маркер наливается цветом, строка подаётся вправо.
    solid = QColor(painter.pen().color())
    for index, (row, right) in enumerate(((6.0, 20.5), (12.0, 17.5), (18.0, 20.5))):
        wave = _bump(t, 0.12 * index, 0.12 * index + 0.55)
        marker = QColor(fill)
        marker.setAlphaF(min(1.0, fill.alphaF() + (solid.alphaF() - fill.alphaF()) * wave))
        painter.setBrush(marker)
        painter.drawRoundedRect(QRectF(3.2, row - 2.1, 4.2, 4.2), 1.3, 1.3)
        shift = 1.4 * wave
        painter.drawLine(QPointF(11.0 + shift, row), QPointF(right + shift * 0.4, row))


def _shield(painter: QPainter, fill: QColor, t: float) -> None:
    # Щит с галочкой: режим, который сейчас защищает соединения. В жесте
    # щит коротко вспыхивает, а галочка рисуется заново одним росчерком.
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
    glow = QColor(fill)
    glow.setAlphaF(min(1.0, fill.alphaF() * (1.0 + 1.3 * _bump(t, 0.35, 1.0))))
    painter.setBrush(glow)
    painter.drawPath(shield)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    stroke = 1.0 if t <= 0.0 else _ease((t - 0.2) / 0.5)
    if stroke <= 0.0:
        return
    first = (QPointF(8.7, 12.1), QPointF(11.0, 14.4))
    second = (QPointF(11.0, 14.4), QPointF(15.4, 9.8))
    split = 0.35
    a = min(1.0, stroke / split)
    painter.drawLine(first[0], first[0] + (first[1] - first[0]) * a)
    if stroke > split:
        b = (stroke - split) / (1.0 - split)
        painter.drawLine(second[0], second[0] + (second[1] - second[0]) * b)


def _star(painter: QPainter, fill: QColor, t: float) -> None:
    _ = t
    painter.setBrush(fill)
    painter.drawPath(build_star_path(QPointF(12.0, 12.6), 9.4))


def _tour(painter: QPainter, fill: QColor, t: float) -> None:
    # Шапочка выпускника: ромб, тулья под ним и кисточка, которая в жесте качается.
    hop = -1.2 * _bump(t, 0.0, 0.4)
    crown = QPainterPath()
    crown.moveTo(6.6, 11.6)
    crown.lineTo(6.6, 15.6)
    crown.cubicTo(6.6, 17.2, 9.0, 18.6, 12.0, 18.6)
    crown.cubicTo(15.0, 18.6, 17.4, 17.2, 17.4, 15.6)
    crown.lineTo(17.4, 11.6)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(crown)
    painter.save()
    painter.translate(0.0, hop)
    cap = QPainterPath()
    cap.moveTo(12.0, 4.6)
    cap.lineTo(21.4, 9.2)
    cap.lineTo(12.0, 13.8)
    cap.lineTo(2.6, 9.2)
    cap.closeSubpath()
    painter.setBrush(fill)
    painter.drawPath(cap)
    swing = 3.0 * math.sin(2.0 * math.pi * 1.5 * t) * (1.0 - t) if t > 0.0 else 0.0
    painter.drawLine(QPointF(21.4, 9.2), QPointF(21.4 + swing, 14.4 - abs(swing) * 0.25))
    painter.restore()


def _wifi(painter: QPainter, fill: QColor, t: float) -> None:
    # Три дуги сигнала и точка. В жесте дуги загораются по очереди снизу вверх.
    _ = fill
    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(painter.pen())
    base = QColor(pen.color())
    center = QPointF(12.0, 18.4)
    for index, radius in enumerate((4.6, 8.6, 12.6)):
        if t > 0.0:
            # Пока жест идёт, дуга сначала гаснет, а потом зажигается в свою очередь.
            lit = max(1.0 - _ease(t / 0.12), _ease((t - 0.14 - 0.18 * index) / 0.25))
            color = QColor(base)
            color.setAlphaF(base.alphaF() * (0.25 + 0.75 * lit))
            pen.setColor(color)
            painter.setPen(pen)
        box = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        # Углы в 1/16 градуса: дуга в 90° с вершиной наверху.
        painter.drawArc(box, 45 * 16, 90 * 16)
    pen.setColor(base)
    painter.setPen(pen)
    painter.setBrush(base)
    painter.drawEllipse(center, 1.1 + 0.6 * _bump(t, 0.0, 0.3), 1.1 + 0.6 * _bump(t, 0.0, 0.3))


def _network_reset(painter: QPainter, fill: QColor, t: float) -> None:
    # Круговая стрелка вокруг узла сети. В жесте стрелка делает полный оборот.
    painter.setBrush(fill)
    painter.drawEllipse(QPointF(12.0, 12.0), 3.0, 3.0)
    painter.save()
    painter.translate(12.0, 12.0)
    # Стрелка нарисована по часовой стрелке — туда же и оборот.
    painter.rotate(360.0 * _ease(t) if t > 0.0 else 0.0)
    painter.translate(-12.0, -12.0)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    ring = QRectF(3.6, 3.6, 16.8, 16.8)
    painter.drawArc(ring, 35 * 16, 250 * 16)
    head = QPainterPath()
    head.moveTo(15.4, 4.2)
    head.lineTo(19.0, 6.9)
    head.lineTo(18.4, 2.6)
    painter.drawPath(head)
    painter.restore()


def _book(painter: QPainter, fill: QColor, t: float) -> None:
    # Раскрытая книга: две страницы и корешок. В жесте перелистывается страница.
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
    if 0.0 < t < 1.0:
        # Лист поворачивается вокруг корешка справа налево; его край — дуга.
        edge = 12.0 + 7.6 * math.cos(math.pi * _ease(t))
        leaf = QPainterPath()
        leaf.moveTo(12.0, 6.8)
        leaf.cubicTo((12.0 + edge) / 2, 5.2, edge, 5.0, edge, 5.6)
        leaf.lineTo(edge, 17.8)
        leaf.cubicTo(edge, 17.6, (12.0 + edge) / 2, 17.8, 12.0, 19.4)
        page = QColor(fill)
        page.setAlphaF(min(1.0, fill.alphaF() * 2.0))
        painter.setBrush(page)
        painter.drawPath(leaf)


_ICONS: dict[str, Callable[[QPainter, QColor, float], None]] = {
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


# Значки со своим коротким жестом; у звезды жест свой — блик (MotionIcon.twinkle).
ANIMATED_LINE_ICONS = frozenset({"preset", "profiles", "mode", "tour", "connection_test", "network_reset", "folder", "docs"})


def line_icon_names() -> tuple[str, ...]:
    return tuple(_ICONS)


def paint_line_icon(painter: QPainter, name: str, rect: QRectF, color: QColor, t: float = 0.0) -> None:
    """Рисует значок ``name`` в прямоугольник ``rect`` цветом ``color``.

    ``t`` — ход жеста от 0 до 1: 0 — значок в покое, остальное — кадр его
    короткой анимации (у каждого значка своя).
    """
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
    draw(painter, fill, max(0.0, min(1.0, float(t))))
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


__all__ = ["ANIMATED_LINE_ICONS", "line_icon_names", "line_icon_pixmap", "paint_line_icon"]
