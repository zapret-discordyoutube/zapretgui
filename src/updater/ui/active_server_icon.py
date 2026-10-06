"""Значок «активный сервер»: залитый кружок с галочкой в цвете акцента.

Раньше активный сервер помечался эмодзи-звездой прямо в тексте. Эмодзи
рисует шрифт системы: он жёлтый при любом акценте, выглядит по-разному на
разных Windows и не попадает в стиль остальных значков программы.

Значок задан векторно (SVG) и красится одним цветом. Галочка — вырез в
кружке, поэтому сквозь неё виден фон строки и второго цвета не нужно.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QLabel


ACTIVE_SERVER_ICON_SIZE = 16
LEGEND_ICON_SIZE = 14

# Роль ячейки с названием сервера: True у активного сервера. По ней страница
# находит строку, которую нужно перекрасить при смене акцента.
ACTIVE_SERVER_ROLE = Qt.ItemDataRole.UserRole + 41

_FALLBACK_COLOR = "#5fcffe"

# Кружок и галочка-вырез (fill-rule: evenodd). Концы галочки скруглены.
_ACTIVE_SERVER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path fill="{color}" fill-rule="evenodd" d="'
    "M8 1a7 7 0 1 1 0 14A7 7 0 0 1 8 1Z"
    "M11.03 5.72a.75.75 0 0 1 0 1.06L7.53 10.28a.75.75 0 0 1-1.06 0"
    "L4.97 8.78a.75.75 0 1 1 1.06-1.06L7 8.69l2.97-2.97a.75.75 0 0 1 1.06 0Z"
    '"/></svg>'
)

# Масштабы экрана, для которых значок рисуется заранее: Qt берёт ближайший.
_ICON_SCALES = (1.0, 1.5, 2.0, 3.0)

_PIXMAP_CACHE: dict[tuple[str, int, int], QPixmap] = {}
_ICON_CACHE: dict[str, QIcon] = {}


def _color_key(color_hex: str) -> str:
    color = QColor(str(color_hex or ""))
    if not color.isValid():
        color = QColor(_FALLBACK_COLOR)
    return color.name(QColor.NameFormat.HexRgb)


def active_server_svg(color_hex: str) -> str:
    """Текст SVG значка в заданном цвете."""
    return _ACTIVE_SERVER_SVG.format(color=_color_key(color_hex))


def active_server_pixmap(color_hex: str, *, size: int = ACTIVE_SERVER_ICON_SIZE, scale: float = 1.0) -> QPixmap:
    """Картинка значка: `size` — в точках интерфейса, `scale` — масштаб экрана."""
    color = _color_key(color_hex)
    side = max(8, int(size))
    scale_key = max(100, int(round(float(scale) * 100)))
    key = (color, side, scale_key)
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached

    ratio = scale_key / 100.0
    device_side = max(1, int(round(side * ratio)))
    pixmap = QPixmap(device_side, device_side)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(active_server_svg(color).encode("utf-8")))
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, device_side, device_side))
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    _PIXMAP_CACHE[key] = pixmap
    return pixmap


def active_server_icon(color_hex: str) -> QIcon:
    """Значок для ячейки таблицы: чёткий на любом масштабе экрана."""
    color = _color_key(color_hex)
    cached = _ICON_CACHE.get(color)
    if cached is not None:
        return cached
    icon = QIcon()
    for scale in _ICON_SCALES:
        icon.addPixmap(active_server_pixmap(color, size=ACTIVE_SERVER_ICON_SIZE, scale=scale))
    _ICON_CACHE[color] = icon
    return icon


class ActiveServerLegendIcon(QLabel):
    """Маленький значок перед подписью «активный» над таблицей серверов."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._color = ""
        self.setFixedSize(LEGEND_ICON_SIZE, LEGEND_ICON_SIZE)
        self.set_color(_FALLBACK_COLOR)

    def set_color(self, color_hex: str) -> None:
        color = _color_key(color_hex)
        if color == self._color:
            return
        self._color = color
        self.setPixmap(active_server_pixmap(color, size=LEGEND_ICON_SIZE, scale=self.devicePixelRatioF()))

    def color(self) -> str:
        return self._color


__all__ = [
    "ACTIVE_SERVER_ICON_SIZE",
    "ACTIVE_SERVER_ROLE",
    "ActiveServerLegendIcon",
    "LEGEND_ICON_SIZE",
    "active_server_icon",
    "active_server_pixmap",
    "active_server_svg",
]
