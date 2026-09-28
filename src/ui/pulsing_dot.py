from __future__ import annotations

import math

from PyQt6.QtCore import QElapsedTimer, QEasingCurve, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled


# Один «удар сердца»: двойной толчок точки (тук-тук) и расходящееся кольцо.
# Удар рисуется с частотой 30 кадров в секунду, а между ударами кадры не
# рисуются вовсе — ждёт только одиночный таймер. В среднем выходит меньше
# 10 перерисовок маленькой точки в секунду, даже если программа открыта весь день.
BEAT_DURATION_MS = 1100
BEAT_REST_MS = 2400
BEAT_FRAME_MS = 33
COLOR_FADE_MS = 380
BEAT_SCALE = 0.16


def _beat_curve(t: float) -> float:
    """Двойной толчок: сильный в начале удара и слабее чуть позже."""

    def bump(center: float, width: float) -> float:
        x = (t - center) / width
        return math.exp(-x * x)

    return min(1.0, bump(0.09, 0.055) + 0.6 * bump(0.27, 0.06))


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class PulsingDot(QWidget):
    """Точка состояния, которая «бьётся», пока процесс работает."""

    def __init__(self, parent=None, *, size: int = 32):
        super().__init__(parent)
        # _color — цвет, который точка показывает по смыслу (итоговый);
        # _shown_color — цвет на экране прямо сейчас, пока идёт перетекание.
        self._color = QColor("#aeb5c1")
        self._shown_color = QColor(self._color)
        self._color_from = QColor(self._color)
        self._pulse_phase = 0.0
        self._is_pulsing = False

        self.setFixedSize(max(12, int(size)), max(12, int(size)))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._beat = QTimer(self)
        self._beat.setInterval(BEAT_FRAME_MS)
        self._beat.timeout.connect(self._on_beat_frame)
        self._beat_clock = QElapsedTimer()

        self._rest_timer = QTimer(self)
        self._rest_timer.setSingleShot(True)
        self._rest_timer.timeout.connect(self._start_beat)

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start.
        self._fade = QVariantAnimation(self)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setDuration(COLOR_FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade_value)

    # ---- публичное API -------------------------------------------------

    def set_color(self, color: str) -> None:
        c = QColor(color)
        if not c.isValid() or c == self._color:
            return
        self._color = c
        if self.isVisible() and are_live_animations_enabled():
            # Цвет перетекает, а не переключается скачком: серый → зелёный.
            self._color_from = QColor(self._shown_color)
            self._fade.stop()
            self._fade.start()
        else:
            self._fade.stop()
            self._shown_color = QColor(c)
            self.update()

    def start_pulse(self) -> None:
        if not self._is_pulsing:
            self._is_pulsing = True
            self._pulse_phase = 0.0
            self._resume()

    def stop_pulse(self) -> None:
        self._is_pulsing = False
        self._halt()
        self.update()

    def is_beating(self) -> bool:
        return self._beat.isActive()

    # ---- жизненный цикл ------------------------------------------------

    def _can_animate(self) -> bool:
        if not self._is_pulsing or not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _resume(self) -> None:
        if self._can_animate() and not self.is_beating() and not self._rest_timer.isActive():
            self._start_beat()

    def _halt(self) -> None:
        self._rest_timer.stop()
        self._beat.stop()
        self._pulse_phase = 0.0

    def _start_beat(self) -> None:
        if not self._can_animate():
            self._halt()
            self.update()
            return
        self._pulse_phase = 0.0
        self._beat_clock.start()
        self._beat.start()

    def _on_beat_frame(self) -> None:
        phase = self._beat_clock.elapsed() / BEAT_DURATION_MS
        if phase < 1.0:
            self._pulse_phase = phase
            self.update()
            return
        self._finish_beat()

    def _finish_beat(self) -> None:
        self._beat.stop()
        self._pulse_phase = 0.0
        self.update()
        if self._can_animate():
            self._rest_timer.start(BEAT_REST_MS)

    def _on_fade_value(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        self._shown_color = _mix(self._color_from, self._color, t)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._resume()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._halt()
        self._fade.stop()
        self._shown_color = QColor(self._color)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window and window.isMinimized():
                self._halt()
            else:
                self._resume()

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        center = QPointF(self.width() / 2, self.height() / 2)
        side = max(12, min(self.width(), self.height()))
        base_r = max(3.0, side * 0.1875)
        glow_extra = max(1.0, side * 0.09375)
        ring_room = max(1.0, side / 2 - base_r - 1)

        phase = self._pulse_phase if self.is_beating() else 0.0
        beat = _beat_curve(phase) if phase > 0.0 else 0.0

        if phase > 0.0:
            # Кольцо стартует от точки и растворяется к краю.
            spread = 1.0 - (1.0 - phase) ** 2
            ring = QColor(self._shown_color)
            ring.setAlphaF(max(0.0, 0.55 * (1.0 - spread)))
            painter.setBrush(ring)
            radius = base_r + ring_room * spread
            painter.drawEllipse(center, radius, radius)

        glow = QColor(self._shown_color)
        glow.setAlphaF(0.42 + 0.2 * beat)
        painter.setBrush(glow)
        glow_r = base_r + glow_extra * (1.0 + 0.6 * beat)
        painter.drawEllipse(center, glow_r, glow_r)

        core_r = base_r * (1.0 + BEAT_SCALE * beat)
        painter.setBrush(self._shown_color)
        painter.drawEllipse(center, core_r, core_r)

        painter.setBrush(QColor(255, 255, 255, 90))
        shine = max(2.0, side * 0.09375) / 2
        painter.drawEllipse(QPointF(center.x() - shine, center.y() - shine - 1), shine, shine)
        painter.end()


# Поток пакетов для карточки «Статус работы»: сквозь точку по верхней дорожке
# идут запросы (до точки серые, после — цвета точки), по нижней обратно летят
# ответы. Кадры идут непрерывно, но только пока карточка видна, окно не свёрнуто
# и анимации включены; область маленькая, поэтому перерисовка дешёвая.
FLOW_WIDTH = 84
FLOW_LANE_GAP = 5
FLOW_RAW_COLOR = QColor(150, 156, 168)
# (направление, скорость в px/с, сдвиги пакетов вдоль дорожки в долях длины)
FLOW_LANES = (
    (1, 34.0, (0.0, 0.31, 0.47, 0.78)),
    (-1, 26.0, (0.12, 0.58, 0.66)),
)


class PacketFlowIndicator(PulsingDot):
    """Точка состояния, сквозь которую бегают пакеты, пока процесс работает."""

    def __init__(self, parent=None, *, size: int = 32, width: int = FLOW_WIDTH):
        super().__init__(parent, size=size)
        self.setFixedSize(max(int(width), self.height()), self.height())
        self._flow_time = 0.0
        self._flow_origin = 0.0

    def _start_beat(self) -> None:
        if self._can_animate():
            # Поток продолжается с того места, где остановился, без рывка.
            self._flow_origin = self._flow_time
        super()._start_beat()

    def _on_beat_frame(self) -> None:
        # Пауз между ударами нет, поэтому на каждом кадре заново проверяем,
        # можно ли ещё анимировать (окно свернули, анимации выключили).
        if not self._can_animate():
            self._halt()
            self.update()
            return
        self._flow_time = self._flow_origin + self._beat_clock.elapsed() / 1000.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        width = float(self.width())
        center = QPointF(width / 2, self.height() / 2)
        side = max(12, self.height())
        base_r = max(3.0, side * 0.1875)
        lanes_y = (center.y() - FLOW_LANE_GAP, center.y() + FLOW_LANE_GAP)

        track = QColor(FLOW_RAW_COLOR)
        track.setAlphaF(0.16)
        painter.setBrush(track)
        for y in lanes_y:
            painter.drawRect(QRectF(2.0, y - 0.5, width - 4.0, 1.0))

        flash = 0.0
        if self._is_pulsing:
            # Без анимаций пакеты остаются на месте, чтобы «работает» всё равно
            # отличалось от «остановлен» не только цветом.
            for (direction, speed, offsets), y in zip(FLOW_LANES, lanes_y):
                for offset in offsets:
                    u = (self._flow_time * speed / width + offset) % 1.0
                    x = u * width if direction > 0 else width - u * width
                    edge = min(1.0, min(x, width - x) / 10.0)
                    passed = x > center.x() if direction > 0 else True
                    color = QColor(self._shown_color if passed else FLOW_RAW_COLOR)
                    flash += 0.5 * math.exp(-(((x - center.x()) / 4.0) ** 2))

                    color.setAlphaF(0.25 * edge)
                    painter.setBrush(color)
                    painter.drawRect(QRectF(x - 3.0 - direction * 6.0, y - 1.0, 6.0, 2.0))
                    color.setAlphaF(edge)
                    painter.setBrush(color)
                    painter.drawRoundedRect(QRectF(x - 3.0, y - 1.5, 6.0, 3.0), 1.5, 1.5)
        flash = min(1.0, flash) if self.is_beating() else 0.0

        glow = QColor(self._shown_color)
        glow.setAlphaF(0.35 + 0.3 * flash)
        painter.setBrush(glow)
        glow_r = (base_r + max(1.0, side * 0.09375)) * (1.0 + 0.5 * flash)
        painter.drawEllipse(center, glow_r, glow_r)

        painter.setBrush(self._shown_color)
        painter.drawEllipse(center, base_r, base_r)

        painter.setBrush(QColor(255, 255, 255, 90))
        shine = max(2.0, side * 0.09375) / 2
        painter.drawEllipse(QPointF(center.x() - shine, center.y() - shine - 1), shine, shine)
        painter.end()
