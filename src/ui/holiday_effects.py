"""Праздничные эффекты главного окна: гирлянда и падающий снег.

Всё рисуется в отдельном прозрачном окне-слое поверх главного окна. Слой не
принимает мышь и фокус, а совмещает его с главным окном сама Windows. Поэтому
кадр анимации перерисовывает только этот слой, а не карточки и кнопки под ним:
раньше эффекты жили внутри главного окна, и каждый кадр заставлял Qt заново
рисовать всё, что лежит под снежинками.

Анимация идёт одним таймером на 30 кадров в секунду. Движение считается по
реально прошедшему времени, поэтому задержка кадра не ускоряет и не замедляет снег.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable
from typing import NamedTuple

from PyQt6.QtCore import (
    QElapsedTimer,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from ui.frame_clock import frame_clock

FRAME_INTERVAL_MS = 33
_MAX_FRAME_DT = 0.1
_FADE_IN_SECONDS = 0.45
_FADE_OUT_SECONDS = 0.3


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else min(value, high)


def _smoothstep(value: float) -> float:
    value = _clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def _blank_pixmap(width: float, height: float, dpr: float) -> QPixmap:
    pixmap = QPixmap(max(1, math.ceil(width * dpr)), max(1, math.ceil(height * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap


class _Sprite(NamedTuple):
    pixmap: QPixmap
    half_width: float
    half_height: float


class _SpriteCache:
    """Готовые картинки снежинок и лампочек под текущий масштаб экрана.

    Каждая картинка рисуется один раз, а в кадре только копируется на слой.
    """

    def __init__(self) -> None:
        self._dpr = 1.0
        self._items: dict[tuple, _Sprite] = {}

    @property
    def device_pixel_ratio(self) -> float:
        return self._dpr

    def set_device_pixel_ratio(self, dpr: float) -> bool:
        dpr = max(1.0, float(dpr))
        if abs(dpr - self._dpr) < 1e-3:
            return False
        self._dpr = dpr
        self._items.clear()
        return True

    def get(self, key: tuple, factory: Callable[[float], QPixmap]) -> _Sprite:
        sprite = self._items.get(key)
        if sprite is None:
            pixmap = factory(self._dpr)
            size = pixmap.deviceIndependentSize()
            sprite = _Sprite(pixmap, size.width() * 0.5, size.height() * 0.5)
            self._items[key] = sprite
        return sprite

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()


# ---------------------------------------------------------------------------
# Гирлянда
# ---------------------------------------------------------------------------

_BULB_COLORS = (
    (255, 84, 84),
    (255, 196, 66),
    (82, 220, 128),
    (86, 162, 255),
    (242, 116, 208),
)
_BULB_TILTS = (-16.0, 0.0, 16.0)
_BULB_CENTER_OFFSET = 9.4
_BULB_HALO_RADIUS = 14.0
_GARLAND_MODE_SECONDS = 11.0


def _bulb_glass_rect() -> QRectF:
    """Стекло лампочки в координатах, где (0, 0) — точка крепления к проводу."""
    return QRectF(-3.9, _BULB_CENTER_OFFSET - 5.7, 7.8, 11.4)


def _paint_bulb_glass(painter: QPainter, rgb: tuple[int, int, int], *, lit: bool) -> None:
    r, g, b = rgb
    glass = _bulb_glass_rect()
    gradient = QRadialGradient(QPointF(-1.2, _BULB_CENTER_OFFSET - 2.0), 7.5)
    if lit:
        gradient.setColorAt(0.0, QColor(min(255, r + 150), min(255, g + 150), min(255, b + 150)))
        gradient.setColorAt(0.5, QColor(r, g, b))
        gradient.setColorAt(1.0, QColor(int(r * 0.72), int(g * 0.72), int(b * 0.72)))
    else:
        gradient.setColorAt(0.0, QColor(int(r * 0.5), int(g * 0.5), int(b * 0.5), 235))
        gradient.setColorAt(1.0, QColor(int(r * 0.22), int(g * 0.22), int(b * 0.22), 235))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(glass)

    painter.setBrush(QColor(255, 255, 255, 190 if lit else 70))
    painter.drawEllipse(QRectF(glass.left() + 1.6, glass.top() + 1.7, 1.9, 3.4))


def _render_lit_bulb(rgb: tuple[int, int, int], tilt: float, dpr: float) -> QPixmap:
    size = _BULB_HALO_RADIUS * 2.0 + 2.0
    pixmap = _blank_pixmap(size, size, dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(size * 0.5, size * 0.5)

    r, g, b = rgb
    halo = QRadialGradient(QPointF(0.0, 0.0), _BULB_HALO_RADIUS)
    halo.setColorAt(0.0, QColor(r, g, b, 130))
    halo.setColorAt(0.45, QColor(r, g, b, 46))
    halo.setColorAt(1.0, QColor(r, g, b, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(halo)
    painter.drawEllipse(QPointF(0.0, 0.0), _BULB_HALO_RADIUS, _BULB_HALO_RADIUS)

    painter.rotate(tilt)
    painter.translate(0.0, -_BULB_CENTER_OFFSET)
    _paint_bulb_glass(painter, rgb, lit=True)
    painter.end()
    return pixmap


class _Bulb:
    __slots__ = ("anchor_x", "anchor_y", "center_x", "center_y", "color", "index", "phase", "speed", "tilt")

    def __init__(self, index: int, anchor_x: float, anchor_y: float, color: int, tilt: int, rng: random.Random):
        self.index = index
        self.anchor_x = anchor_x
        self.anchor_y = anchor_y
        self.color = color
        self.tilt = tilt
        angle = math.radians(_BULB_TILTS[tilt])
        self.center_x = anchor_x - math.sin(angle) * _BULB_CENTER_OFFSET
        self.center_y = anchor_y + math.cos(angle) * _BULB_CENTER_OFFSET
        self.phase = rng.uniform(0.0, math.tau)
        self.speed = rng.uniform(1.6, 3.2)


class _Garland:
    """Провод с провисами между крючками и лампочками, которые переливаются.

    Провод, цоколи и погасшие лампочки нарисованы заранее одной картинкой.
    В кадре поверх неё с разной яркостью рисуется только свечение лампочек.
    """

    BAND_HEIGHT = 44
    _WIRE_TOP = 3.0
    _SAG = 11.0

    def __init__(self) -> None:
        self._width = 0
        self._top = 0
        self._time = 0.0
        self._bulbs: list[_Bulb] = []
        self._hooks: list[float] = []
        self._static: QPixmap | None = None
        self._static_dpr = 0.0

    @property
    def bulbs(self) -> list[_Bulb]:
        return self._bulbs

    def band_rect(self) -> QRect:
        return QRect(0, self._top, self._width, self.BAND_HEIGHT)

    def layout(self, width: int, top: int) -> None:
        width = int(width)
        top = max(0, int(top))
        if width == self._width and top == self._top:
            return
        self._top = top
        if width == self._width:
            return
        self._width = width
        self._static = None
        self._bulbs.clear()
        self._hooks.clear()
        if width < 120:
            return

        swags = max(2, round(width / 210))
        span = width / swags
        per_swag = max(3, round(span / 32))
        rng = random.Random(swags * 1009 + per_swag)
        self._hooks = [index * span for index in range(swags + 1)]
        index = 0
        for swag in range(swags):
            start = swag * span
            for slot in range(per_swag):
                t = (slot + 0.5) / per_swag
                x = start + span * t
                y = self._wire_y(t)
                tilt = rng.choice((0, 0, 1, 2)) if slot % 2 else rng.choice((0, 1, 2, 2))
                self._bulbs.append(_Bulb(index, x, y, index % len(_BULB_COLORS), tilt, rng))
                index += 1

    def clear(self) -> None:
        self._width = 0
        self._bulbs.clear()
        self._hooks.clear()
        self._static = None

    def invalidate(self) -> None:
        self._static = None

    def step(self, dt: float) -> None:
        self._time += dt

    def brightness(self, bulb: _Bulb) -> float:
        """Яркость лампочки: бегущая волна, чередование и мерцание по очереди."""
        t = self._time
        modes = (
            0.5 + 0.5 * math.sin(t * 2.6 - bulb.index * 0.55),
            0.5 + 0.5 * math.sin(t * 1.7 + (bulb.index % 2) * math.pi),
            0.5 + 0.5 * math.sin(t * bulb.speed + bulb.phase),
        )
        cycle = t / _GARLAND_MODE_SECONDS
        current = int(cycle) % len(modes)
        blend = _smoothstep((cycle - int(cycle) - 0.82) / 0.18)
        value = modes[current] * (1.0 - blend) + modes[(current + 1) % len(modes)] * blend
        return 0.12 + 0.88 * _smoothstep(value)

    def paint(self, painter: QPainter, sprites: _SpriteCache, opacity: float) -> None:
        if not self._bulbs or opacity <= 0.0:
            return
        dpr = sprites.device_pixel_ratio
        if self._static is None or self._static_dpr != dpr:
            self._static = self._render_static(dpr)
            self._static_dpr = dpr

        top = float(self._top)
        painter.setOpacity(opacity)
        painter.drawPixmap(QPointF(0.0, top), self._static)
        for bulb in self._bulbs:
            rgb = _BULB_COLORS[bulb.color]
            tilt = _BULB_TILTS[bulb.tilt]
            sprite = sprites.get(("bulb", bulb.color, bulb.tilt), lambda d, c=rgb, a=tilt: _render_lit_bulb(c, a, d))
            painter.setOpacity(opacity * self.brightness(bulb))
            painter.drawPixmap(
                QPointF(bulb.center_x - sprite.half_width, top + bulb.center_y - sprite.half_height),
                sprite.pixmap,
            )

    def _wire_y(self, t: float) -> float:
        return self._WIRE_TOP + self._SAG * 4.0 * t * (1.0 - t)

    def _render_static(self, dpr: float) -> QPixmap:
        pixmap = _blank_pixmap(self._width, self.BAND_HEIGHT, dpr)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        wire = QPainterPath()
        for left, right in zip(self._hooks, self._hooks[1:]):
            middle = (left + right) * 0.5
            # Квадратичная кривая с контрольной точкой на двойной глубине даёт провис ровно _SAG.
            wire.moveTo(left, self._WIRE_TOP)
            wire.quadTo(middle, self._WIRE_TOP + self._SAG * 2.0, right, self._WIRE_TOP)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(24, 46, 32, 240), 1.8))
        painter.drawPath(wire)
        painter.setPen(QPen(QColor(70, 112, 80, 170), 0.8))
        painter.drawPath(wire.translated(0.0, -0.6))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 36, 26, 230))
        for hook in self._hooks:
            painter.drawEllipse(QPointF(hook, self._WIRE_TOP), 2.0, 2.0)

        for bulb in self._bulbs:
            painter.save()
            painter.translate(bulb.anchor_x, bulb.anchor_y)
            painter.rotate(_BULB_TILTS[bulb.tilt])
            _paint_bulb_glass(painter, _BULB_COLORS[bulb.color], lit=False)
            socket = QLinearGradient(-2.6, 0.0, 2.6, 0.0)
            socket.setColorAt(0.0, QColor(26, 48, 34))
            socket.setColorAt(0.5, QColor(62, 98, 72))
            socket.setColorAt(1.0, QColor(22, 40, 28))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(socket)
            painter.drawRoundedRect(QRectF(-2.6, -0.8, 5.2, 4.8), 1.2, 1.2)
            painter.restore()

        painter.end()
        return pixmap


# ---------------------------------------------------------------------------
# Снег
# ---------------------------------------------------------------------------


class _LayerSpec(NamedTuple):
    name: str
    size: tuple[float, float]
    speed: tuple[float, float]
    alpha: tuple[float, float]
    sway: tuple[float, float]
    sway_speed: tuple[float, float]
    wind: float
    area_per_flake: int
    min_count: int
    max_count: int
    deposit: float


# Дальние снежинки мелкие и размытые, средние — мягкие точки со свечением,
# ближние — крупные шестилучевые кристаллы, которые медленно вращаются.
_FAR = _LayerSpec("far", (1.0, 1.9), (14.0, 24.0), (0.4, 0.7), (3.0, 8.0), (0.25, 0.55), 0.45, 9000, 14, 85, 0.0)
_MID = _LayerSpec("mid", (2.0, 3.2), (28.0, 40.0), (0.6, 0.9), (6.0, 14.0), (0.3, 0.6), 0.75, 16000, 8, 55, 2.2)
_NEAR = _LayerSpec("near", (5.0, 8.0), (44.0, 62.0), (0.75, 0.95), (10.0, 20.0), (0.2, 0.4), 1.0, 90000, 3, 10, 4.0)
_SNOW_LAYERS = (_FAR, _MID, _NEAR)
_CRYSTAL_ANGLE_STEPS = 15


def _render_dot(size: float, soft: bool, dpr: float) -> QPixmap:
    radius = size * (1.3 if soft else 1.9)
    extent = radius * 2.0 + 2.0
    pixmap = _blank_pixmap(extent, extent, dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    center = QPointF(extent * 0.5, extent * 0.5)
    gradient = QRadialGradient(center, radius)
    if soft:
        gradient.setColorAt(0.0, QColor(255, 255, 255, 230))
        gradient.setColorAt(0.55, QColor(232, 242, 255, 120))
        gradient.setColorAt(1.0, QColor(232, 242, 255, 0))
    else:
        gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
        gradient.setColorAt(0.42, QColor(250, 252, 255, 235))
        gradient.setColorAt(0.55, QColor(220, 234, 255, 70))
        gradient.setColorAt(1.0, QColor(220, 234, 255, 0))
    painter.setBrush(gradient)
    painter.drawEllipse(center, radius, radius)
    painter.end()
    return pixmap


def _render_crystal(radius: float, angle: float, dpr: float) -> QPixmap:
    extent = radius * 2.0 + 6.0
    pixmap = _blank_pixmap(extent, extent, dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(extent * 0.5, extent * 0.5)

    glow = QRadialGradient(QPointF(0.0, 0.0), radius + 2.5)
    glow.setColorAt(0.0, QColor(225, 238, 255, 80))
    glow.setColorAt(1.0, QColor(225, 238, 255, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(0.0, 0.0), radius + 2.5, radius + 2.5)

    pen = QPen(QColor(255, 255, 255, 240), max(0.9, radius * 0.16))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.rotate(angle)
    diagonal = math.sqrt(0.5)
    for _ in range(6):
        painter.drawLine(QPointF(0.0, 0.0), QPointF(0.0, -radius))
        for position, length in ((0.5, 0.36), (0.78, 0.24)):
            y = -radius * position
            branch = radius * length
            painter.drawLine(QPointF(0.0, y), QPointF(branch * diagonal, y - branch * diagonal))
            painter.drawLine(QPointF(0.0, y), QPointF(-branch * diagonal, y - branch * diagonal))
        painter.rotate(60.0)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 250))
    painter.drawEllipse(QPointF(0.0, 0.0), radius * 0.2, radius * 0.2)
    painter.end()
    return pixmap


class _Flake:
    __slots__ = ("alpha", "angle", "bucket", "draw_x", "phase", "size", "speed", "spin", "sway", "sway_speed", "x", "y")

    def __init__(self, spec: _LayerSpec, rng: random.Random, x: float, y: float):
        self.size = rng.uniform(*spec.size)
        self.bucket = round(self.size * 2.0) / 2.0 if spec is _NEAR else round(self.size * 4.0) / 4.0
        self.speed = rng.uniform(*spec.speed)
        self.alpha = rng.uniform(*spec.alpha)
        self.sway = rng.uniform(*spec.sway)
        self.sway_speed = rng.uniform(*spec.sway_speed) * math.tau
        self.phase = rng.uniform(0.0, math.tau)
        self.angle = rng.uniform(0.0, 60.0)
        self.spin = rng.uniform(18.0, 45.0) * rng.choice((-1.0, 1.0))
        self.x = x
        self.y = y
        self.draw_x = x + math.sin(self.phase) * self.sway


class _SnowDrift:
    """Сугроб внизу окна: высота снега по столбикам шириной в несколько пикселей.

    Картинка сугроба перерисовывается не чаще нескольких раз в секунду и только
    если на него что-то упало; в кадре она просто копируется на слой.
    """

    STEP = 6.0
    MAX_HEIGHT = 19.0
    _REBUILD_SECONDS = 0.3
    _MAX_SLOPE = 1.0
    _PIXMAP_HEIGHT = MAX_HEIGHT + 6.0

    def __init__(self) -> None:
        self._width = 0
        self._heights: list[float] = []
        self._caps: list[float] = []
        self._pixmap: QPixmap | None = None
        self._pixmap_dpr = 0.0
        self._dirty = False
        self._since_rebuild = 0.0
        rng = random.Random()
        self._noise = [(rng.uniform(0.004, 0.012), rng.uniform(0.0, math.tau)) for _ in range(3)]

    @property
    def heights(self) -> list[float]:
        return self._heights

    @property
    def caps(self) -> list[float]:
        return self._caps

    def resize(self, width: int) -> None:
        width = int(width)
        if width == self._width:
            return
        columns = max(2, int(width / self.STEP) + 2)
        old = self._heights
        if old:
            scale = (len(old) - 1) / (columns - 1)
            resized = []
            for index in range(columns):
                position = index * scale
                left = int(position)
                right = min(left + 1, len(old) - 1)
                frac = position - left
                resized.append(old[left] * (1.0 - frac) + old[right] * frac)
            self._heights = resized
        else:
            self._heights = [0.0] * columns
        self._width = width
        self._caps = [self._cap_at(index * self.STEP, width) for index in range(columns)]
        self._heights = [min(height, cap) for height, cap in zip(self._heights, self._caps)]
        self._dirty = True

    def clear(self) -> None:
        self._heights = [0.0] * len(self._heights)
        self._pixmap = None
        self._dirty = False

    def height_at(self, x: float) -> float:
        if not self._heights:
            return 0.0
        position = _clamp(x / self.STEP, 0.0, len(self._heights) - 1.0)
        left = int(position)
        right = min(left + 1, len(self._heights) - 1)
        frac = position - left
        return self._heights[left] * (1.0 - frac) + self._heights[right] * frac

    def deposit(self, x: float, amount: float) -> None:
        if not self._heights or amount <= 0.0:
            return
        center = round(x / self.STEP)
        for offset, weight in ((-2, 0.1), (-1, 0.2), (0, 0.4), (1, 0.2), (2, 0.1)):
            index = center + offset
            if 0 <= index < len(self._heights):
                self._heights[index] = min(self._caps[index], self._heights[index] + amount * weight)
        self._dirty = True

    def step(self, dt: float) -> None:
        self._since_rebuild += dt

    def pixmap(self, dpr: float) -> QPixmap | None:
        if not self._heights:
            return None
        stale = self._pixmap is None or self._pixmap_dpr != dpr
        if stale or (self._dirty and self._since_rebuild >= self._REBUILD_SECONDS):
            self._settle()
            self._pixmap = self._render(dpr) if max(self._heights) > 0.2 else None
            self._pixmap_dpr = dpr
            self._dirty = False
            self._since_rebuild = 0.0
        return self._pixmap

    @property
    def pixmap_height(self) -> float:
        return self._PIXMAP_HEIGHT

    def _cap_at(self, x: float, width: int) -> float:
        wave = sum(math.sin(x * frequency + phase) for frequency, phase in self._noise) / len(self._noise)
        cap = 9.0 + 5.0 * (0.5 + 0.5 * wave)
        # В углах окна снега собирается больше, как у рамы настоящего окна.
        edge = min(x, max(0.0, width - x))
        cap += 5.0 * max(0.0, 1.0 - edge / 90.0)
        return min(cap, self.MAX_HEIGHT)

    def _settle(self) -> None:
        """Осыпает слишком крутые склоны, чтобы сугроб оставался плавным."""
        heights = self._heights
        for _ in range(2):
            for index in range(len(heights) - 1):
                diff = heights[index] - heights[index + 1]
                if diff > self._MAX_SLOPE:
                    move = (diff - self._MAX_SLOPE) * 0.5
                    heights[index] -= move
                    heights[index + 1] = min(self._caps[index + 1], heights[index + 1] + move)
                elif -diff > self._MAX_SLOPE:
                    move = (-diff - self._MAX_SLOPE) * 0.5
                    heights[index + 1] -= move
                    heights[index] = min(self._caps[index], heights[index] + move)

    def _render(self, dpr: float) -> QPixmap:
        height = self._PIXMAP_HEIGHT
        pixmap = _blank_pixmap(self._width, height, dpr)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        points = [QPointF(index * self.STEP, height - value) for index, value in enumerate(self._heights)]
        surface = QPainterPath(points[0])
        for current, following in itertools.pairwise(points):
            surface.quadTo(current, (current + following) * 0.5)
        surface.lineTo(points[-1])

        body = QPainterPath(surface)
        body.lineTo(points[-1].x(), height)
        body.lineTo(0.0, height)
        body.closeSubpath()

        fill = QLinearGradient(0.0, height - self.MAX_HEIGHT, 0.0, height)
        fill.setColorAt(0.0, QColor(255, 255, 255, 240))
        fill.setColorAt(1.0, QColor(206, 222, 244, 225))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(body)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(160, 188, 225, 110), 2.2))
        painter.drawPath(surface.translated(0.0, 1.4))
        painter.setPen(QPen(QColor(255, 255, 255, 255), 1.1))
        painter.drawPath(surface)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        for index in range(3, len(self._heights), 7):
            value = self._heights[index]
            if value > 5.0:
                painter.drawEllipse(QPointF(index * self.STEP + 1.5, height - value + 3.0), 0.8, 0.8)

        painter.end()
        return pixmap


class _SnowField:
    """Три слоя падающего снега с лёгким переменным ветром и сугробом внизу."""

    def __init__(self) -> None:
        self._rng = random.Random()
        self._width = 0
        self._height = 0
        self._time = 0.0
        self._flakes: dict[str, list[_Flake]] = {spec.name: [] for spec in _SNOW_LAYERS}
        self.drift = _SnowDrift()

    def flakes(self, spec: _LayerSpec) -> list[_Flake]:
        return self._flakes[spec.name]

    def flake_count(self) -> int:
        return sum(len(flakes) for flakes in self._flakes.values())

    @staticmethod
    def target_count(spec: _LayerSpec, width: int, height: int) -> int:
        return int(_clamp((width * height) // spec.area_per_flake, spec.min_count, spec.max_count))

    def resize(self, width: int, height: int) -> None:
        width = int(width)
        height = int(height)
        if width == self._width and height == self._height:
            return
        self._width = width
        self._height = height
        self.drift.resize(width)

    def populate(self, *, seed_visible: bool) -> None:
        """Доводит число снежинок до нормы для текущего размера окна."""
        if self._width <= 0 or self._height <= 0:
            return
        for spec in _SNOW_LAYERS:
            flakes = self._flakes[spec.name]
            target = self.target_count(spec, self._width, self._height)
            if len(flakes) > target:
                del flakes[target:]
            while len(flakes) < target:
                if seed_visible:
                    y = self._rng.uniform(-20.0, float(self._height))
                else:
                    y = self._rng.uniform(-self._height * 0.5, -10.0)
                flakes.append(_Flake(spec, self._rng, self._rng.uniform(0.0, float(self._width)), y))

    def clear(self) -> None:
        for flakes in self._flakes.values():
            flakes.clear()
        self.drift.clear()

    def wind(self) -> float:
        t = self._time
        return 9.0 * math.sin(t * 0.11) + 5.0 * math.sin(t * 0.37 + 1.3)

    def step(self, dt: float, *, spawning: bool) -> None:
        self._time += dt
        width = float(self._width)
        floor = float(self._height)
        if width <= 0.0 or floor <= 0.0:
            return
        wind = self.wind()
        drift = self.drift
        rng = self._rng
        for spec in _SNOW_LAYERS:
            flakes = self._flakes[spec.name]
            layer_wind = wind * spec.wind
            finished: list[_Flake] | None = None
            for flake in flakes:
                flake.y += flake.speed * dt
                flake.x += layer_wind * dt
                if flake.x < -24.0:
                    flake.x += width + 48.0
                elif flake.x > width + 24.0:
                    flake.x -= width + 48.0
                flake.phase += flake.sway_speed * dt
                if flake.phase > math.tau:
                    flake.phase -= math.tau
                flake.draw_x = flake.x + math.sin(flake.phase) * flake.sway
                if spec.deposit:
                    flake.angle = (flake.angle + flake.spin * dt) % 60.0
                    landed = flake.y + flake.size * 0.5 >= floor - drift.height_at(flake.draw_x)
                    if landed:
                        drift.deposit(flake.draw_x, spec.deposit * flake.size / spec.size[1])
                else:
                    landed = flake.y - flake.size * 2.0 > floor
                if not landed:
                    continue
                if spawning:
                    flake.x = rng.uniform(0.0, width)
                    flake.y = rng.uniform(-40.0, -flake.size * 2.0)
                else:
                    if finished is None:
                        finished = []
                    finished.append(flake)
            if finished:
                self._flakes[spec.name] = [flake for flake in flakes if flake not in finished]
        drift.step(dt)

    def paint_back(self, painter: QPainter, sprites: _SpriteCache, opacity: float) -> None:
        """Дальний и средний снег, а под ним сугроб: ближние кристаллы рисуются отдельно, поверх."""
        for spec, soft in ((_FAR, True), (_MID, False)):
            for flake in self._flakes[spec.name]:
                sprite = sprites.get(
                    ("dot", soft, flake.bucket),
                    lambda d, s=flake.bucket, f=soft: _render_dot(s, f, d),
                )
                painter.setOpacity(opacity * flake.alpha)
                painter.drawPixmap(QPointF(flake.draw_x - sprite.half_width, flake.y - sprite.half_height), sprite.pixmap)

        drift = self.drift.pixmap(sprites.device_pixel_ratio)
        if drift is not None:
            painter.setOpacity(opacity)
            painter.drawPixmap(QPointF(0.0, self._height - self.drift.pixmap_height), drift)

    def paint_front(self, painter: QPainter, sprites: _SpriteCache, opacity: float) -> None:
        for flake in self._flakes[_NEAR.name]:
            step = int(flake.angle / 60.0 * _CRYSTAL_ANGLE_STEPS) % _CRYSTAL_ANGLE_STEPS
            sprite = sprites.get(
                ("crystal", flake.bucket, step),
                lambda d, r=flake.bucket, a=step * 60.0 / _CRYSTAL_ANGLE_STEPS: _render_crystal(r, a, d),
            )
            painter.setOpacity(opacity * flake.alpha)
            painter.drawPixmap(QPointF(flake.draw_x - sprite.half_width, flake.y - sprite.half_height), sprite.pixmap)


# ---------------------------------------------------------------------------
# Окно-слой и управление
# ---------------------------------------------------------------------------


class HolidayOverlayWindow(QWidget):
    """Прозрачное окно поверх главного: мышь и фокус проходят сквозь него.

    Окно принадлежит главному окну, поэтому Windows держит его сразу над ним,
    не показывает на панели задач и сворачивает вместе с ним.
    """

    def __init__(self, host: QWidget, painter_callback: Callable[[QPainter], None]):
        super().__init__(
            host,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self._painter_callback = painter_callback
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            self._painter_callback(painter)
        finally:
            painter.end()


class HolidayEffectsManager(QObject):
    """Держит слой с эффектами в такт с главным окном: место, размер, видимость, пауза."""

    _WATCHED_EVENTS = frozenset(
        {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.WindowStateChange,
        }
    )

    def __init__(self, host_window: QWidget):
        super().__init__(host_window)
        self._host = host_window
        self._closed = False
        self._animation_active = True
        self._garland_enabled = False
        self._snow_enabled = False
        self._garland_opacity = 0.0
        self._snow_opacity = 0.0

        self._sprites = _SpriteCache()
        self._garland = _Garland()
        self._snow = _SnowField()
        self._overlay = HolidayOverlayWindow(host_window, self._paint)

        self._clock = QElapsedTimer()
        # Кадры — от общего такта приложения: при заблокированном сеансе и
        # выключенном экране он стоит, и снег не идёт в пустоту; перерисовка
        # сливается с остальными живыми анимациями окна.
        self._timer = frame_clock().subscribe(self._on_frame, interval_ms=FRAME_INTERVAL_MS, owner=self)

        host_window.installEventFilter(self)

    # --- состояние для окна и тестов -------------------------------------

    @property
    def overlay(self) -> HolidayOverlayWindow:
        return self._overlay

    def is_garland_enabled(self) -> bool:
        return self._garland_enabled

    def is_snowflakes_enabled(self) -> bool:
        return self._snow_enabled

    def is_running(self) -> bool:
        return self._timer.isActive()

    # --- управление ------------------------------------------------------

    def set_garland_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._garland_enabled:
            self._refresh()
            return
        self._garland_enabled = enabled
        self._refresh()

    def set_snowflakes_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._snow_enabled:
            self._refresh()
            return
        if enabled and self._snow_opacity <= 0.0:
            # Первые снежинки сразу раскладываем по всему окну, а не только над ним.
            self._snow.clear()
            self._sync_geometry()
            self._snow.populate(seed_visible=True)
        self._snow_enabled = enabled
        self._refresh()

    def set_animation_active(self, active: bool) -> None:
        self._animation_active = bool(active)
        self._refresh()

    def sync_geometry(self) -> None:
        if self._closed or not self._needs_overlay():
            return
        self._sync_geometry()
        if self._overlay.isVisible():
            self._overlay.update()

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        try:
            self._host.removeEventFilter(self)
        except RuntimeError:
            pass
        self._garland_enabled = False
        self._snow_enabled = False
        self._release_resources()
        self._overlay.hide()
        self._overlay.deleteLater()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._host and not self._closed:
            event_type = event.type()
            if event_type in self._WATCHED_EVENTS:
                if event_type in (QEvent.Type.Move, QEvent.Type.Resize):
                    if self._overlay.isVisible():
                        self._sync_geometry()
                else:
                    self._refresh()
                    if event_type != QEvent.Type.Hide:
                        # Состояние окна после показа и разворачивания окончательно
                        # устанавливается чуть позже самого события.
                        QTimer.singleShot(0, self._refresh)
        return False

    # --- внутреннее ------------------------------------------------------

    def _host_shown(self) -> bool:
        try:
            return bool(self._host.isVisible()) and not bool(self._host.isMinimized())
        except RuntimeError:
            return False

    def _can_animate(self) -> bool:
        return self._animation_active and self._host_shown()

    def _needs_overlay(self) -> bool:
        return (
            self._garland_enabled
            or self._snow_enabled
            or self._garland_opacity > 0.0
            or self._snow_opacity > 0.0
        )

    def _refresh(self) -> None:
        """Приводит слой и таймер в соответствие с включёнными эффектами и окном."""
        if self._closed:
            return
        if not self._can_animate():
            # Без анимации плавное появление и исчезновение не нужны.
            self._garland_opacity = 1.0 if self._garland_enabled else 0.0
            self._snow_opacity = 1.0 if self._snow_enabled else 0.0

        if not self._needs_overlay():
            self._timer.stop()
            self._overlay.hide()
            self._release_resources()
            return

        if not self._host_shown():
            self._timer.stop()
            self._overlay.hide()
            return

        self._sync_geometry()
        if not self._overlay.isVisible():
            self._overlay.show()

        if self._animation_active:
            if not self._timer.isActive():
                self._clock.restart()
                self._timer.start()
        else:
            self._timer.stop()
        self._overlay.update()

    def _sync_geometry(self) -> None:
        host = self._host
        width = int(host.width())
        height = int(host.height())
        if width <= 0 or height <= 0:
            return
        rect = QRect(host.mapToGlobal(QPoint(0, 0)), host.size())
        if self._overlay.geometry() != rect:
            self._overlay.setGeometry(rect)

        if self._sprites.set_device_pixel_ratio(self._device_pixel_ratio()):
            self._garland.invalidate()

        title_bar = getattr(host, "titleBar", None)
        top = int(title_bar.height()) if title_bar is not None else 0
        if self._garland_enabled or self._garland_opacity > 0.0:
            self._garland.layout(width, top)
        self._snow.resize(width, height)
        if self._snow_enabled:
            self._snow.populate(seed_visible=False)

    def _device_pixel_ratio(self) -> float:
        try:
            return float(self._overlay.devicePixelRatioF())
        except RuntimeError:
            return 1.0

    def _release_resources(self) -> None:
        self._snow.clear()
        self._garland.clear()
        self._sprites.clear()

    def _on_frame(self) -> None:
        if self._closed:
            return
        dt = _clamp(self._clock.restart() / 1000.0, 0.0, _MAX_FRAME_DT)
        self._garland_opacity = self._fade(self._garland_opacity, self._garland_enabled, dt)
        self._snow_opacity = self._fade(self._snow_opacity, self._snow_enabled, dt)

        if self._garland_opacity > 0.0:
            self._garland.step(dt)
        if self._snow_opacity > 0.0:
            self._snow.step(dt, spawning=self._snow_enabled)

        if not self._needs_overlay():
            self._refresh()
            return
        if self._snow_opacity > 0.0:
            self._overlay.update()
        else:
            self._overlay.update(self._garland.band_rect())

    @staticmethod
    def _fade(value: float, enabled: bool, dt: float) -> float:
        if enabled:
            return min(1.0, value + dt / _FADE_IN_SECONDS)
        return max(0.0, value - dt / _FADE_OUT_SECONDS)

    def _paint(self, painter: QPainter) -> None:
        if self._snow_opacity > 0.0:
            self._snow.paint_back(painter, self._sprites, self._snow_opacity)
        if self._garland_opacity > 0.0:
            self._garland.paint(painter, self._sprites, self._garland_opacity)
        if self._snow_opacity > 0.0:
            self._snow.paint_front(painter, self._sprites, self._snow_opacity)


__all__ = ["FRAME_INTERVAL_MS", "HolidayEffectsManager", "HolidayOverlayWindow"]
