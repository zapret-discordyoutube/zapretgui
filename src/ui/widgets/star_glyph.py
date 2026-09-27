"""Золотая звезда, нарисованная кодом, а не эмодзи или шрифтовой иконкой.

Эмодзи в Windows выглядят по-разному в разных версиях и шрифтах, а здесь
форма и цвет всегда одинаковые и чёткие на любом масштабе экрана.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication


STAR_LIGHT_GOLD = "#fde68a"
STAR_DEEP_GOLD = "#f59e0b"
STAR_OUTLINE_GOLD = "#b45309"


def build_star_path(center: QPointF, outer_radius: float, *, inner_ratio: float = 0.46) -> QPainterPath:
    """Пятиконечная звезда остриём вверх со слегка скруглёнными вершинами."""
    points: list[QPointF] = []
    inner_radius = outer_radius * inner_ratio
    for index in range(10):
        radius = outer_radius if index % 2 == 0 else inner_radius
        angle = -math.pi / 2 + index * math.pi / 5
        points.append(QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle)))

    path = QPainterPath()
    # Вершины скругляем через середины рёбер: звезда выглядит мягче и не
    # «колется» на маленьком размере.
    mids = [
        QPointF((points[i].x() + points[(i + 1) % 10].x()) / 2, (points[i].y() + points[(i + 1) % 10].y()) / 2)
        for i in range(10)
    ]
    path.moveTo(mids[-1])
    for index in range(10):
        path.quadTo(points[index], mids[index])
    path.closeSubpath()
    return path


def paint_star(painter: QPainter, center: QPointF, outer_radius: float, *, outlined: bool = False) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = build_star_path(center, outer_radius)

    gradient = QLinearGradient(
        QPointF(center.x(), center.y() - outer_radius),
        QPointF(center.x(), center.y() + outer_radius),
    )
    gradient.setColorAt(0.0, QColor(STAR_LIGHT_GOLD))
    gradient.setColorAt(1.0, QColor(STAR_DEEP_GOLD))
    if outlined:
        painter.setPen(QPen(QColor(STAR_OUTLINE_GOLD), max(0.8, outer_radius * 0.09)))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawPath(path)

    # Маленький блик в верхней части звезды.
    shine = QColor(255, 255, 255, 150)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(shine)
    r = outer_radius * 0.14
    painter.drawEllipse(QPointF(center.x() - outer_radius * 0.14, center.y() - outer_radius * 0.2), r, r)
    painter.restore()


def render_star_icon(size: int, *, outlined: bool = False) -> QIcon:
    """Готовая иконка звезды нужного размера с учётом масштаба экрана."""
    return QIcon(render_star_pixmap(size, outlined=outlined))


def render_star_pixmap(size: int, *, outlined: bool = False) -> QPixmap:
    """Картинка звезды нужного размера с учётом масштаба экрана."""
    app = QApplication.instance()
    dpr = 1.0
    try:
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            dpr = float(screen.devicePixelRatio() or 1.0)
    except Exception:
        dpr = 1.0
    side = max(4, int(size))
    physical = max(4, round(side * dpr))
    pixmap = QPixmap(physical, physical)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    paint_star(painter, QPointF(side / 2, side / 2 + side * 0.04), side * 0.5, outlined=outlined)
    painter.end()
    return pixmap


__all__ = ["build_star_path", "paint_star", "render_star_icon", "render_star_pixmap"]
