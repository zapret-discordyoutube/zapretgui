"""Значки стратегий, нарисованные линиями.

И на плитке списка, и на странице подробностей стоит значок способа обхода
(``strategy_icon``: подделка, нарезка, перестановка…), в его углу — оценка
человека: зелёная галочка «работает» или красный крестик «не работает». На
плитке подложка значка нейтральная, цветной только сам рисунок.

``state_icon`` — отдельный значок состояния (кружок, галочка, крестик) для
мест, где способ обхода не важен.

Значки рисуются кистью, а не шрифтом значков: они не зависят от того, какие
шрифты попали в сборку программы. Каждый вид рисуется один раз и хранится
готовой картинкой — плитка только выводит её.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

# Значок рисуется на поле 16×16 и растягивается до нужного размера.
_FIELD = 16.0
_WORKS = QColor("#49a35f")
_NOT_WORKS = QColor("#d85c5c")
_CACHE: dict[tuple, QPixmap] = {}
_CACHE_MAX = 256


def _pen(color: QColor, width: float = 1.5, *, dashed: bool = False) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dashed:
        pen.setDashPattern([1.3, 2.0])
    return pen


def _line(painter: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _arrow(painter: QPainter, x1: float, x2: float, y: float) -> None:
    """Горизонтальная стрелка от x1 к x2."""
    _line(painter, x1, y, x2, y)
    head = 2.2 if x2 > x1 else -2.2
    _line(painter, x2, y, x2 - head, y - 2.0)
    _line(painter, x2, y, x2 - head, y + 2.0)


def _fake(painter: QPainter, color: QColor) -> None:
    """Подделка: настоящий пакет и его пунктирный двойник позади."""
    painter.setPen(_pen(color, dashed=True))
    painter.drawRoundedRect(QRectF(5.5, 2.5, 8.0, 8.0), 1.8, 1.8)
    painter.setPen(_pen(color))
    painter.setBrush(color)
    painter.drawRoundedRect(QRectF(2.5, 6.0, 7.5, 7.5), 1.8, 1.8)


def _split(painter: QPainter, color: QColor) -> None:
    """Нарезка: одна полоса данных, разрезанная на части."""
    painter.setPen(_pen(color, 2.6))
    for x1, x2 in ((2.0, 4.6), (7.0, 9.0), (11.4, 14.0)):
        _line(painter, x1, 8.0, x2, 8.0)


def _disorder(painter: QPainter, color: QColor) -> None:
    """Перестановка: части уходят в другом порядке."""
    painter.setPen(_pen(color))
    _arrow(painter, 2.5, 13.5, 5.0)
    _arrow(painter, 13.5, 2.5, 11.0)


def _fake_split(painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color, dashed=True))
    painter.drawRoundedRect(QRectF(3.0, 2.5, 10.0, 5.0), 1.6, 1.6)
    painter.setPen(_pen(color, 2.4))
    for x1, x2 in ((2.5, 4.8), (7.0, 9.0), (11.2, 13.5)):
        _line(painter, x1, 11.8, x2, 11.8)


def _fake_disorder(painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color, dashed=True))
    painter.drawRoundedRect(QRectF(3.0, 2.0, 10.0, 4.6), 1.6, 1.6)
    painter.setPen(_pen(color))
    _arrow(painter, 2.5, 13.5, 9.6)
    _arrow(painter, 13.5, 2.5, 13.4)


def _host(painter: QPainter, color: QColor) -> None:
    """Имя сайта: бирка с отверстием."""
    path = QPainterPath()
    path.moveTo(2.5, 8.0)
    path.lineTo(6.5, 3.5)
    path.lineTo(13.5, 3.5)
    path.lineTo(13.5, 12.5)
    path.lineTo(6.5, 12.5)
    path.closeSubpath()
    painter.setPen(_pen(color))
    painter.drawPath(path)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(7.4, 8.0), 1.1, 1.1)


def _send(painter: QPainter, color: QColor) -> None:
    """Дополнительная отправка: лишний пакет летит впереди."""
    painter.setPen(_pen(color))
    for x in (3.0, 8.0):
        _line(painter, x, 4.0, x + 4.0, 8.0)
        _line(painter, x + 4.0, 8.0, x, 12.0)


def _length(painter: QPainter, color: QColor) -> None:
    """Изменение длины: линейка."""
    painter.setPen(_pen(color))
    painter.drawRoundedRect(QRectF(2.0, 5.0, 12.0, 6.0), 1.4, 1.4)
    for x in (5.0, 8.0, 11.0):
        _line(painter, x, 5.0, x, 7.6)


def _other(painter: QPainter, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for x in (4.0, 8.0, 12.0):
        painter.drawEllipse(QPointF(x, 8.0), 1.3, 1.3)


_GLYPHS = {
    "fake": _fake,
    "split": _split,
    "disorder": _disorder,
    "fake_split": _fake_split,
    "fake_disorder": _fake_disorder,
    "host": _host,
    "send": _send,
    "fake_udplen": _length,
}


def _mark(painter: QPainter, size: float, rating: str, backdrop: QColor) -> None:
    """Оценка человека в правом нижнем углу значка."""
    radius = size * 0.21
    center = QPointF(size - radius - 0.5, size - radius - 0.5)
    # Ободок цвета плитки отделяет отметку от значка способа.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(backdrop)
    painter.drawEllipse(center, radius + 1.4, radius + 1.4)
    painter.setBrush(_WORKS if rating == "work" else _NOT_WORKS)
    painter.drawEllipse(center, radius, radius)
    painter.setPen(_pen(QColor("#ffffff"), max(1.2, size * 0.06)))
    x, y, step = center.x(), center.y(), radius * 0.48
    if rating == "work":
        painter.drawPolyline(
            [QPointF(x - step, y), QPointF(x - step * 0.25, y + step * 0.8), QPointF(x + step, y - step * 0.7)]
        )
    else:
        _line(painter, x - step * 0.8, y - step * 0.8, x + step * 0.8, y + step * 0.8)
        _line(painter, x - step * 0.8, y + step * 0.8, x + step * 0.8, y - step * 0.8)


def strategy_icon(
    family_key: str, color: str, rating: str, size: int, device_ratio: float, backdrop: str, plate: str = ""
) -> QPixmap:
    """Готовая картинка значка стратегии: способ обхода и оценка человека.

    backdrop — цвет плитки под значком: им обведена отметка оценки.
    plate — цвет подложки; пусто — подложка того же цвета, что рисунок.
    """
    key = (family_key, color, rating, int(size), round(float(device_ratio), 2), backdrop, plate)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    ratio = max(1.0, float(device_ratio))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    tint = QColor(color) if QColor(color).isValid() else QColor("#9aa6b2")
    fill = QColor(plate) if QColor(plate).isValid() else QColor(tint)
    fill.setAlpha(38)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)

    # Рисунок способа занимает середину подложки.
    inner = size * 0.62
    painter.save()
    painter.translate((size - inner) / 2, (size - inner) / 2)
    painter.scale(inner / _FIELD, inner / _FIELD)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _GLYPHS.get(family_key, _other)(painter, tint)
    painter.restore()

    if rating in ("work", "notwork"):
        _mark(painter, float(size), rating, QColor(backdrop) if QColor(backdrop).isValid() else QColor("#2b2b2b"))
    painter.end()

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = pixmap
    return pixmap


def state_icon(rating: str, is_current: bool, size: int, device_ratio: float, accent: str, idle: str) -> QPixmap:
    """Готовая картинка значка состояния стратегии на плитке списка.

    accent — цвет акцента темы (выбранная стратегия), idle — приглушённый цвет
    текста (стратегия, которую ещё не пробовали).
    """
    key = ("state", rating, bool(is_current), int(size), round(float(device_ratio), 2), accent, idle)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    ratio = max(1.0, float(device_ratio))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    center = QPointF(size / 2, size / 2)
    radius = size * 0.34
    line = max(1.3, size * 0.06)
    if rating in ("work", "notwork"):
        color = _WORKS if rating == "work" else _NOT_WORKS
        fill = QColor(color)
        fill.setAlpha(46)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(center, radius, radius)
        painter.setPen(_pen(color, line * 1.15))
        x, y, step = center.x(), center.y(), radius * 0.46
        if rating == "work":
            painter.drawPolyline(
                [QPointF(x - step, y), QPointF(x - step * 0.25, y + step * 0.8), QPointF(x + step, y - step * 0.7)]
            )
        else:
            _line(painter, x - step * 0.8, y - step * 0.8, x + step * 0.8, y + step * 0.8)
            _line(painter, x - step * 0.8, y + step * 0.8, x + step * 0.8, y - step * 0.8)
    else:
        color = QColor(accent if is_current else idle)
        if not color.isValid():
            color = QColor("#9aa6b2")
        ring = QColor(color)
        if not is_current:
            ring.setAlpha(120)
        painter.setPen(_pen(ring, line))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius - line / 2, radius - line / 2)
        if is_current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(center, radius * 0.42, radius * 0.42)
    painter.end()

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = pixmap
    return pixmap
