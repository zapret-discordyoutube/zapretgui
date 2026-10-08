"""Внешний вид меню трея: размеры, цвета и значки.

Меню рисуется своим кодом, без стилей библиотеки: один набор цветов для тёмной
темы, один для светлой, и значки тонкими линиями в квадрате 16×16.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF


WIDTH = 292
ROW_HEIGHT = 32
STATUS_HEIGHT = 54
SEPARATOR_HEIGHT = 9
PADDING = 5
RADIUS = 8
SHADOW = 14
ICON_SIZE = 16
ICON_LEFT = 16
TEXT_LEFT = 44
TEXT_LEFT_PLAIN = 18
SIDE_GAP = 14
HOVER_INSET = 4
HOVER_RADIUS = 5
MAX_LIST_ROWS = 12
SCREEN_GAP = 8
FONT_PX = 13
FONT_SMALL_PX = 12


@dataclass(frozen=True, slots=True)
class MenuPalette:
    background: QColor
    border: QColor
    text: QColor
    muted: QColor
    disabled: QColor
    hover: QColor
    pressed: QColor
    separator: QColor
    scroll_thumb: QColor
    idle_dot: QColor
    accent: QColor


def _rgba(red: int, green: int, blue: int, alpha: float) -> QColor:
    color = QColor(red, green, blue)
    color.setAlphaF(alpha)
    return color


def menu_palette() -> MenuPalette:
    try:
        from qfluentwidgets import isDarkTheme, themeColor

        dark = bool(isDarkTheme())
        accent = QColor(themeColor())
    except Exception:
        dark = True
        accent = QColor("#60cdff")
    if dark:
        return MenuPalette(
            background=QColor(44, 44, 44),
            border=_rgba(255, 255, 255, 0.10),
            text=_rgba(255, 255, 255, 0.92),
            muted=_rgba(255, 255, 255, 0.58),
            disabled=_rgba(255, 255, 255, 0.34),
            hover=_rgba(255, 255, 255, 0.075),
            pressed=_rgba(255, 255, 255, 0.045),
            separator=_rgba(255, 255, 255, 0.085),
            scroll_thumb=_rgba(255, 255, 255, 0.30),
            idle_dot=QColor("#9aa0a6"),
            accent=accent,
        )
    return MenuPalette(
        background=QColor(249, 249, 249),
        border=_rgba(0, 0, 0, 0.14),
        text=_rgba(0, 0, 0, 0.90),
        muted=_rgba(0, 0, 0, 0.58),
        disabled=_rgba(0, 0, 0, 0.34),
        hover=_rgba(0, 0, 0, 0.055),
        pressed=_rgba(0, 0, 0, 0.035),
        separator=_rgba(0, 0, 0, 0.085),
        scroll_thumb=_rgba(0, 0, 0, 0.32),
        idle_dot=QColor("#8a9096"),
        accent=accent,
    )


# ---- значки ---------------------------------------------------------------


def _line(painter: QPainter, *points: tuple[float, float]) -> None:
    painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))


def _play(painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(5.0, 3.4)
    path.lineTo(12.6, 8.0)
    path.lineTo(5.0, 12.6)
    path.closeSubpath()
    painter.drawPath(path)


def _stop(painter: QPainter, color: QColor) -> None:
    painter.drawRoundedRect(QRectF(3.8, 3.8, 8.4, 8.4), 1.6, 1.6)


def _restart(painter: QPainter, color: QColor) -> None:
    painter.drawArc(QRectF(3.0, 3.0, 10.0, 10.0), 40 * 16, 280 * 16)
    _line(painter, (12.9, 2.3), (12.3, 5.2), (9.4, 4.7))


def _presets(painter: QPainter, color: QColor) -> None:
    for y in (4.0, 8.0, 12.0):
        painter.drawPoint(QPointF(3.0, y))
        _line(painter, (6.0, y), (13.2, y))


def _window(painter: QPainter, color: QColor) -> None:
    painter.drawRoundedRect(QRectF(2.3, 3.2, 11.4, 9.6), 1.8, 1.8)
    _line(painter, (2.6, 6.2), (13.4, 6.2))


def _send(painter: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(2.4, 7.5)
    path.lineTo(13.6, 2.8)
    path.lineTo(10.3, 13.2)
    path.lineTo(7.5, 9.1)
    path.closeSubpath()
    painter.drawPath(path)
    _line(painter, (7.5, 9.1), (13.6, 2.8))


def _opacity(painter: QPainter, color: QColor) -> None:
    circle = QRectF(3.0, 3.0, 10.0, 10.0)
    painter.drawEllipse(circle)
    half = QPainterPath()
    half.moveTo(8.0, 3.0)
    half.arcTo(circle, 90, 180)
    half.closeSubpath()
    fill = QColor(color)
    fill.setAlphaF(color.alphaF() * 0.55)
    painter.fillPath(half, fill)


def _console(painter: QPainter, color: QColor) -> None:
    painter.drawRoundedRect(QRectF(2.0, 3.0, 12.0, 10.0), 1.8, 1.8)
    _line(painter, (4.8, 6.4), (6.9, 8.0), (4.8, 9.6))
    _line(painter, (8.2, 10.0), (11.2, 10.0))


def _exit(painter: QPainter, color: QColor) -> None:
    _line(painter, (9.4, 3.0), (3.4, 3.0), (3.4, 13.0), (9.4, 13.0))
    _line(painter, (7.0, 8.0), (13.6, 8.0))
    _line(painter, (11.4, 5.8), (13.6, 8.0), (11.4, 10.2))


def _power(painter: QPainter, color: QColor) -> None:
    painter.drawArc(QRectF(3.0, 3.6, 10.0, 10.0), 120 * 16, 300 * 16)
    _line(painter, (8.0, 2.4), (8.0, 7.6))


def _chevron_right(painter: QPainter, color: QColor) -> None:
    _line(painter, (6.2, 4.2), (10.0, 8.0), (6.2, 11.8))


def _chevron_left(painter: QPainter, color: QColor) -> None:
    _line(painter, (9.8, 4.2), (6.0, 8.0), (9.8, 11.8))


def _search(painter: QPainter, color: QColor) -> None:
    painter.drawEllipse(QPointF(7.0, 7.0), 4.0, 4.0)
    _line(painter, (10.0, 10.0), (13.2, 13.2))


_ICONS = {
    "play": _play,
    "stop": _stop,
    "restart": _restart,
    "presets": _presets,
    "window": _window,
    "send": _send,
    "opacity": _opacity,
    "console": _console,
    "exit": _exit,
    "power": _power,
    "chevron_right": _chevron_right,
    "chevron_left": _chevron_left,
    "search": _search,
}


def tray_icon_names() -> tuple[str, ...]:
    return tuple(_ICONS)


def paint_tray_icon(painter: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Рисует значок name в квадрате rect линиями цвета color."""
    draw = _ICONS.get(name)
    if draw is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 16.0, rect.height() / 16.0)
    pen = QPen(color, 1.25)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter, color)
    painter.restore()
