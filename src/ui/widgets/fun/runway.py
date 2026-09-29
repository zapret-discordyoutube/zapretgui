"""Полоса загрузки, по которой бежит логотип программы.

- Закрашенная часть — градиент цвета акцента, по ней бегает светлый перелив.
- Пока размер неизвестен (``set_indeterminate(True)``), по дорожке бегает
  отрезок, а логотип бежит вместе с ним туда и обратно.
- Логотип стоит над краем закрашенной части: на бегу подпрыгивает и
  покачивается, за ним тает след из точек. Процент растёт плавно, а не рывками.
- ``finish()`` — радостный прыжок с оборотом в конце дорожки, ``fail()`` —
  красная полоса и грустно наклонённый логотип.

Кадры идут, только пока виджет виден, окно не свёрнуто и включены «живые
анимации». Без них полоса и логотип просто стоят на своём месте.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget

from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled
from ui.theme_refresh import ThemeRefreshBinding

RUNWAY_TRACK_HEIGHT = 10
RUNWAY_LOGO_SIZE = 34
# Один «шаг» бега: подскок и покачивание повторяются с этим периодом.
_STRIDE_MS = 520
_SHIMMER_MS = 1600
_BOUNCE_MS = 2200
_FINISH_MS = 1100
_TRAIL_DOTS = 5
_FAIL_COLOR = "#ff6b6b"

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"


def _ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


class UpdateRunway(QWidget):
    def __init__(self, parent=None, *, icon: QIcon | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Над дорожкой — место под логотип и его подскок.
        self.setFixedHeight(int(RUNWAY_LOGO_SIZE * 1.5) + RUNWAY_TRACK_HEIGHT + 6)
        self.setMinimumWidth(RUNWAY_LOGO_SIZE * 4)
        self._icon = icon
        self._pixmap = QPixmap()
        self._state = STATE_RUNNING
        self._indeterminate = True
        self._target = 0.0
        self._shown = 0.0
        self._clock_ms = 0.0
        self._finish_t = 0.0
        self._accent = QColor("#60cdff")
        self._track = QColor(127, 127, 127, 50)

        # Один бесконечный «такт»: от него считаются и бег, и перелив, и
        # плавный рост процента. Отдельных таймеров нет.
        self._tick = QVariantAnimation(self)
        self._tick.setStartValue(0.0)
        self._tick.setEndValue(1.0)
        self._tick.setDuration(1000)
        self._tick.setLoopCount(-1)
        self._tick.currentLoopChanged.connect(self._on_loop)
        self._tick.valueChanged.connect(self._on_tick)
        self._loops = 0

        self._finish_anim = QVariantAnimation(self)
        self._finish_anim.setStartValue(0.0)
        self._finish_anim.setEndValue(1.0)
        self._finish_anim.setDuration(_FINISH_MS)
        self._finish_anim.valueChanged.connect(self._on_finish_value)

        self._theme_refresh = ThemeRefreshBinding(self, self._apply_theme_refresh)
        self._apply_theme_refresh()
        self._announce()

    # --- управление -----------------------------------------------------------

    def state(self) -> str:
        return self._state

    def is_indeterminate(self) -> bool:
        return self._indeterminate

    def value(self) -> float:
        return self._target

    def displayed_value(self) -> float:
        return self._shown

    def is_animating(self) -> bool:
        return self._tick.state() == QVariantAnimation.State.Running

    def reset(self) -> None:
        self._state = STATE_RUNNING
        self._indeterminate = True
        self._target = 0.0
        self._shown = 0.0
        self._finish_t = 0.0
        self._finish_anim.stop()
        self._sync_ticking()
        self._announce()
        self.update()

    def set_indeterminate(self, indeterminate: bool) -> None:
        self._indeterminate = bool(indeterminate)
        self._sync_ticking()
        self._announce()
        self.update()

    def set_value(self, percent: float) -> None:
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            return
        self._indeterminate = False
        self._target = value
        if not self._can_animate():
            self._shown = value
        self._sync_ticking()
        self._announce()
        self.update()

    def finish(self) -> None:
        self._state = STATE_DONE
        self._indeterminate = False
        self._target = 100.0
        self._shown = 100.0
        self._sync_ticking()
        self._finish_t = 0.0
        if self._can_animate():
            self._finish_anim.stop()
            self._finish_anim.start()
        else:
            self._finish_t = 1.0
        self._announce()
        self.update()

    def fail(self) -> None:
        self._state = STATE_FAILED
        self._indeterminate = False
        self._finish_anim.stop()
        self._sync_ticking()
        self._announce()
        self.update()

    # --- анимация -------------------------------------------------------------

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _sync_ticking(self) -> None:
        should_run = self._state == STATE_RUNNING and self._can_animate()
        running = self.is_animating()
        if should_run and not running:
            self._loops = 0
            self._tick.start()
        elif not should_run and running:
            self._tick.stop()
            self._shown = self._target

    def _on_loop(self, loop: int) -> None:
        self._loops = int(loop)

    def _on_tick(self, value) -> None:
        try:
            fraction = float(value)
        except (TypeError, ValueError):
            return
        self._clock_ms = (self._loops + fraction) * 1000.0
        # Процент догоняет настоящий плавно: логотип не телепортируется.
        gap = self._target - self._shown
        self._shown = self._target if abs(gap) < 0.05 else self._shown + gap * 0.12
        self.update()

    def _on_finish_value(self, value) -> None:
        try:
            self._finish_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_ticking()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._tick.stop()
        self._shown = self._target
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_ticking()

    # --- геометрия ------------------------------------------------------------

    def _track_rect(self) -> QRectF:
        margin = RUNWAY_LOGO_SIZE / 2
        top = self.height() - RUNWAY_TRACK_HEIGHT - 3
        return QRectF(margin, top, max(self.width() - margin * 2, 1.0), RUNWAY_TRACK_HEIGHT)

    def _bounce_phase(self) -> float:
        """0..1..0 — положение бегущего отрезка, пока размер неизвестен."""
        t = (self._clock_ms % _BOUNCE_MS) / _BOUNCE_MS
        return 0.5 - 0.5 * math.cos(2 * math.pi * t)

    def runner_x(self) -> float:
        """Центр логотипа по горизонтали."""
        track = self._track_rect()
        if self._indeterminate and self._state == STATE_RUNNING:
            segment = track.width() * 0.28
            return track.left() + segment / 2 + (track.width() - segment) * self._bounce_phase()
        return track.left() + track.width() * self._shown / 100.0

    def runner_pose(self) -> tuple[float, float, float]:
        """(подскок в пикселях вверх, наклон в градусах, масштаб по высоте)."""
        size = float(RUNWAY_LOGO_SIZE)
        if self._state == STATE_FAILED:
            return 0.0, -16.0, 0.94
        if self._state == STATE_DONE:
            t = self._finish_t
            if t >= 1.0:
                return 0.0, 0.0, 1.0
            if t < 0.15:
                return -0.05 * size * (t / 0.15), 0.0, 1.0 - 0.12 * (t / 0.15)
            k = (t - 0.15) / 0.85
            return size * 0.45 * math.sin(math.pi * k), 360.0 * _ease_out_back(min(k, 1.0)), 1.0
        if not self.is_animating():
            return 0.0, 0.0, 1.0
        stride = (self._clock_ms % _STRIDE_MS) / _STRIDE_MS
        hop = size * 0.16 * abs(math.sin(math.pi * stride))
        # Покачивание вдвое медленнее шага: влево на одном шаге, вправо на другом.
        sway = 9.0 * math.sin(math.pi * (self._clock_ms % (_STRIDE_MS * 2)) / _STRIDE_MS)
        squash = 1.0 - 0.07 * (1.0 - abs(math.sin(math.pi * stride)))
        # Наклон вперёд по ходу бега.
        lean = 6.0 if not self._indeterminate else 6.0 * (1 if self._bounce_direction() > 0 else -1)
        return hop, sway + lean, squash

    def _bounce_direction(self) -> float:
        t = (self._clock_ms % _BOUNCE_MS) / _BOUNCE_MS
        return math.sin(2 * math.pi * t)

    # --- отрисовка ------------------------------------------------------------

    def _apply_theme_refresh(self, tokens=None, force: bool = False) -> None:
        _ = force
        try:
            from ui.theme import get_theme_tokens, to_qcolor

            tokens = tokens or get_theme_tokens()
            self._accent = to_qcolor(tokens.accent_hex, "#60cdff")
            track = QColor(0, 0, 0, 34) if tokens.is_light else QColor(255, 255, 255, 30)
            self._track = track
        except Exception:
            self._accent = QColor("#60cdff")
        self.update()

    def _current_pixmap(self) -> QPixmap:
        if self._pixmap.isNull():
            icon = self._icon or QApplication.windowIcon()
            if icon is not None and not icon.isNull():
                ratio = self.devicePixelRatioF() or 1.0
                side = int(RUNWAY_LOGO_SIZE * ratio)
                self._pixmap = icon.pixmap(side, side)
                self._pixmap.setDevicePixelRatio(ratio)
        return self._pixmap

    def _fill_color(self) -> QColor:
        return QColor(_FAIL_COLOR) if self._state == STATE_FAILED else QColor(self._accent)

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        track = self._track_rect()
        radius = track.height() / 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._track)
        painter.drawRoundedRect(track, radius, radius)

        fill = self._fill_rect(track)
        if fill.width() > 0.5:
            self._paint_fill(painter, fill, radius)

        self._paint_trail(painter, track)
        self._paint_runner(painter, track)
        painter.end()

    def _fill_rect(self, track: QRectF) -> QRectF:
        if self._indeterminate and self._state == STATE_RUNNING:
            segment = track.width() * 0.28
            left = track.left() + (track.width() - segment) * self._bounce_phase()
            return QRectF(left, track.top(), segment, track.height())
        return QRectF(track.left(), track.top(), track.width() * self._shown / 100.0, track.height())

    def _paint_fill(self, painter: QPainter, fill: QRectF, radius: float) -> None:
        base = self._fill_color()
        light = QColor(base).lighter(135)
        gradient = QLinearGradient(fill.topLeft(), fill.topRight())
        gradient.setColorAt(0.0, QColor(base).darker(115))
        gradient.setColorAt(1.0, light)
        path = QPainterPath()
        path.addRoundedRect(fill, radius, radius)
        painter.fillPath(path, gradient)

        if self._state != STATE_RUNNING or not self.is_animating():
            return
        # Перелив: светлая полоса пробегает по закрашенной части слева направо.
        t = (self._clock_ms % _SHIMMER_MS) / _SHIMMER_MS
        band = max(fill.width() * 0.35, 36.0)
        center = fill.left() - band + (fill.width() + band * 2) * t
        shine = QLinearGradient(QPointF(center - band, 0), QPointF(center + band, 0))
        clear = QColor(255, 255, 255, 0)
        shine.setColorAt(0.0, clear)
        shine.setColorAt(0.5, QColor(255, 255, 255, 120))
        shine.setColorAt(1.0, clear)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(fill, shine)
        painter.restore()

    def _paint_trail(self, painter: QPainter, track: QRectF) -> None:
        if self._state != STATE_RUNNING or not self.is_animating():
            return
        x = self.runner_x()
        direction = 1.0
        if self._indeterminate:
            direction = 1.0 if self._bounce_direction() > 0 else -1.0
        color = QColor(self._fill_color())
        y = track.top() - 5
        for index in range(1, _TRAIL_DOTS + 1):
            wobble = math.sin((self._clock_ms / 90.0) + index) * 1.5
            color.setAlphaF(max(0.0, 0.5 - index * 0.09))
            painter.setBrush(color)
            dot = 3.2 - index * 0.4
            painter.drawEllipse(
                QPointF(x - direction * (RUNWAY_LOGO_SIZE * 0.35 + index * 7), y + wobble),
                dot,
                dot,
            )

    def _paint_runner(self, painter: QPainter, track: QRectF) -> None:
        pixmap = self._current_pixmap()
        if pixmap.isNull():
            return
        hop, angle, squash = self.runner_pose()
        side = float(RUNWAY_LOGO_SIZE)
        x = min(max(self.runner_x(), side / 2), self.width() - side / 2)
        feet = QPointF(x, track.top() - 2 - hop)
        painter.save()
        if self._state == STATE_FAILED:
            painter.setOpacity(0.75)
        painter.translate(feet)
        painter.scale(2.0 - squash, squash)
        painter.translate(0, -side / 2)
        painter.rotate(angle)
        painter.drawPixmap(QRectF(-side / 2, -side / 2, side, side), pixmap, QRectF(pixmap.rect()))
        painter.restore()

    def _announce(self) -> None:
        if self._state == STATE_DONE:
            text = "Полоса загрузки: готово"
        elif self._state == STATE_FAILED:
            text = "Полоса загрузки: ошибка"
        elif self._indeterminate:
            text = "Полоса загрузки: подготовка"
        else:
            text = f"Полоса загрузки: {int(self._target)}%"
        set_state_text(self, text)


__all__ = ["STATE_DONE", "STATE_FAILED", "STATE_RUNNING", "UpdateRunway"]
