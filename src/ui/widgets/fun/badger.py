"""Медоед, нарисованный кодом: моргает, смотрит по сторонам и машет лапами.

У обычного талисмана (``Mascot``) вместо тела картинка значка программы:
её можно только поворачивать и подбрасывать целиком. Здесь медоед собран
из частей — тело, голова с белой «шапкой», уши, глаза, мордочка и две
передние лапы, — поэтому каждая часть двигается сама:

- глаза моргают раз в несколько секунд (иногда дважды подряд) и смотрят
  туда, что сейчас происходит: на стену, когда медоед кидает пакеты, или
  по сторонам, когда он суетится;
- лапы качаются в такт дыханию, замахиваются при броске, по очереди
  перебирают, пока идёт запуск, и радостно взлетают вверх при включении;
- грустный медоед прикрывает глаза и опускает лапы, насторожённый —
  широко открывает глаза и прижимает лапы к груди.

Настроения, жесты и правила экономии процессора те же, что у ``Mascot``:
кадры идут только во время жеста или моргания, а между ними стоит
одиночный таймер.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from ui.widgets.fun.mascot import GESTURE_TOSS, MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_SAD, Mascot


# Цвета фирменного синего медоеда со значка программы.
BODY_LIGHT = QColor("#3b9cff")
BODY_DARK = QColor("#0b4fc0")
CAP_LIGHT = QColor("#e9f5ff")
CAP_DARK = QColor("#9fd2ff")
MUZZLE = QColor("#7cc0ff")
INK = QColor("#0a1f44")
PAW_DARK = QColor("#0a3f9c")

# Моргание: закрыть и открыть глаза; пауза между морганиями — случайная.
BLINK_MS = 190
BLINK_PAUSE_MIN_MS = 2400
BLINK_PAUSE_MAX_MS = 5600
DOUBLE_BLINK_CHANCE = 0.25

# Лапа висит вниз при угле 0; положительный угол уводит кисть к середине тела.
PAW_REST = 14.0
SHOULDER_Y = 60.0
SHOULDER_X = 15.0
ARM_LENGTH = 15.0


def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class DrawnBadger(Mascot):
    """Талисман-медоед из частей. API тот же, что у ``Mascot``."""

    def __init__(self, parent=None, *, size: int = 44) -> None:
        super().__init__(parent, size=size)
        self._blink = 0.0
        self._blink_left = 0
        self._blink_anim = QVariantAnimation(self)
        self._blink_anim.setStartValue(0.0)
        self._blink_anim.setEndValue(1.0)
        self._blink_anim.setDuration(BLINK_MS)
        self._blink_anim.valueChanged.connect(self._on_blink_value)
        self._blink_anim.finished.connect(self._on_blink_finished)
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self.blink)

    # ---- моргание ------------------------------------------------------

    def blink(self) -> None:
        """Моргнуть сейчас (кадры идут только эти доли секунды)."""
        if not self._can_animate():
            return
        if self._blink_anim.state() == QVariantAnimation.State.Running:
            return
        self._blink_left = 1 if random.random() < DOUBLE_BLINK_CHANCE else 0
        self._blink_anim.start()

    def blink_progress(self) -> float:
        return self._blink

    def _on_blink_value(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        # Веки быстро закрываются и чуть медленнее открываются.
        self._blink = min(1.0, t / 0.4) if t < 0.4 else max(0.0, 1.0 - (t - 0.4) / 0.6)
        self.update()

    def _on_blink_finished(self) -> None:
        self._blink = 0.0
        self.update()
        if self._blink_left > 0:
            self._blink_left -= 1
            QTimer.singleShot(90, self._blink_anim.start)
            return
        self._schedule_blink()

    def _schedule_blink(self) -> None:
        self._blink_timer.stop()
        if self._can_animate():
            self._blink_timer.start(random.randint(BLINK_PAUSE_MIN_MS, BLINK_PAUSE_MAX_MS))

    def is_blink_scheduled(self) -> bool:
        return self._blink_timer.isActive()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_blink()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._blink_timer.stop()
        self._blink_anim.stop()
        self._blink = 0.0
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        window = self.window()
        if window is not None and window.isMinimized():
            self._blink_timer.stop()
            self._blink_anim.stop()
            self._blink = 0.0
        elif not self._blink_timer.isActive():
            self._schedule_blink()

    # ---- позы частей ---------------------------------------------------

    def paw_angles(self) -> tuple[float, float]:
        """Углы левой и правой лапы в градусах (0 — висит вниз)."""
        t = self._t
        g = self._gesture
        left = right = PAW_REST
        if g == MOOD_BUSY:
            # Перебирает лапами по очереди.
            swing = 34.0 * math.sin(2 * math.pi * t * 2)
            return PAW_REST + 20.0 + swing, PAW_REST + 20.0 - swing
        if g == MOOD_HAPPY:
            # Обе лапы взлетают вверх и машут.
            up = math.sin(math.pi * min(1.0, t / 0.8))
            wave = 18.0 * math.sin(2 * math.pi * t * 3) * up
            return PAW_REST + 150.0 * up + wave, PAW_REST + 150.0 * up - wave
        if g == GESTURE_TOSS:
            # Правая лапа (к стене) замахивается за голову и резко бросает вперёд.
            if t < 0.35:
                right = PAW_REST + 160.0 * _ease_in_out(t / 0.35)
            elif t < 0.55:
                right = PAW_REST + 160.0 - 210.0 * _ease_in_out((t - 0.35) / 0.2)
            else:
                right = PAW_REST - 50.0 + 50.0 * _ease_in_out((t - 0.55) / 0.45)
            return PAW_REST + 10.0, right
        if g == MOOD_ALARM:
            # Вздрогнул: лапы дёрнулись к груди.
            jolt = math.sin(math.pi * t)
            return PAW_REST + 70.0 * jolt, PAW_REST + 70.0 * jolt
        if self._mood == MOOD_SAD:
            return 2.0, 2.0
        if self._mood == MOOD_ALARM:
            # Насторожился: лапы прижаты к груди.
            return PAW_REST + 55.0, PAW_REST + 55.0
        # Спокоен: лапы чуть покачиваются в такт дыханию.
        sway = 13.0 * self._breath
        return left + sway, right - sway

    def look_offset(self) -> float:
        """Куда смотрят зрачки: -1 влево, 1 вправо (к стене и сайтам)."""
        g = self._gesture
        if g == GESTURE_TOSS or self._mood == MOOD_ALARM:
            return 0.9
        if g == MOOD_BUSY:
            return math.sin(2 * math.pi * self._t)
        if g == "look":
            return math.sin(2 * math.pi * self._t)
        return 0.25

    def eye_openness(self) -> float:
        """Насколько открыты глаза: 0 — закрыты, 1 — обычно, больше 1 — широко."""
        if self._gesture == MOOD_HAPPY:
            return 0.0
        base = 1.0
        if self._mood == MOOD_SAD:
            base = 0.45
        elif self._mood == MOOD_ALARM or self._gesture == MOOD_ALARM:
            base = 1.18
        return base * (1.0 - self._blink)

    def set_breath(self, value: float) -> None:
        # Дыхание двигает и лапы, поэтому перерисовываем его и при чуть меньшем шаге.
        value = max(-1.0, min(1.0, float(value)))
        if abs(value - self._breath) < 0.04:
            return
        self._breath = value
        if not self._gesture:
            self.update()

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        dy, angle, sx, sy = self.pose()
        side = float(self._side)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Опорная точка — низ лап: медоед приседает и прыгает от «пола».
        painter.translate(self.width() / 2, self.height() - 2 + dy * side)
        painter.scale(sx, sy)
        painter.translate(0, -side / 2)
        painter.rotate(angle)
        painter.scale(side / 100.0, side / 100.0)
        painter.translate(-50.0, -50.0)
        self.paint_badger(painter)
        painter.end()

    def paint_badger(self, painter: QPainter) -> None:
        """Рисует медоеда в квадрате 100×100 (низ лап — на y=100)."""
        painter.setPen(Qt.PenStyle.NoPen)
        left_paw, right_paw = self.paw_angles()

        # Задние лапы и тело.
        painter.setBrush(PAW_DARK)
        painter.drawEllipse(QPointF(35.0, 96.0), 11.0, 5.0)
        painter.drawEllipse(QPointF(65.0, 96.0), 11.0, 5.0)
        body = QLinearGradient(QPointF(30.0, 45.0), QPointF(70.0, 98.0))
        body.setColorAt(0.0, BODY_LIGHT)
        body.setColorAt(1.0, BODY_DARK)
        painter.setBrush(body)
        path = QPainterPath()
        path.moveTo(50.0, 44.0)
        path.cubicTo(74.0, 44.0, 82.0, 66.0, 79.0, 82.0)
        path.cubicTo(77.0, 95.0, 64.0, 98.0, 50.0, 98.0)
        path.cubicTo(36.0, 98.0, 23.0, 95.0, 21.0, 82.0)
        path.cubicTo(18.0, 66.0, 26.0, 44.0, 50.0, 44.0)
        painter.drawPath(path)
        # Светлая «мантия» медоеда спускается от головы по бокам спины.
        mantle_side = QLinearGradient(QPointF(50.0, 44.0), QPointF(50.0, 92.0))
        mantle_side.setColorAt(0.0, CAP_DARK)
        mantle_side.setColorAt(1.0, QColor(CAP_DARK.red(), CAP_DARK.green(), CAP_DARK.blue(), 0))
        painter.setBrush(mantle_side)
        for sign in (-1.0, 1.0):
            stripe = QPainterPath()
            stripe.moveTo(50.0 + sign * 22.0, 46.0)
            stripe.cubicTo(50.0 + sign * 31.0, 54.0, 50.0 + sign * 31.0, 74.0, 50.0 + sign * 27.0, 90.0)
            stripe.cubicTo(50.0 + sign * 26.0, 74.0, 50.0 + sign * 24.0, 58.0, 50.0 + sign * 17.0, 48.0)
            stripe.closeSubpath()
            painter.drawPath(stripe)
        belly = QColor(MUZZLE)
        belly.setAlphaF(0.45)
        painter.setBrush(belly)
        painter.drawEllipse(QPointF(50.0, 78.0), 14.0, 16.0)

        # Голова: маленькие ушки, тёмная морда и светлая «шапка» сверху до бровей.
        for ex in (29.0, 71.0):
            painter.setBrush(BODY_DARK)
            painter.drawEllipse(QPointF(ex, 21.0), 6.0, 6.0)
            painter.setBrush(MUZZLE)
            painter.drawEllipse(QPointF(ex, 21.5), 2.8, 2.8)
        head = QRadialGradient(QPointF(46.0, 40.0), 30.0)
        head.setColorAt(0.0, BODY_LIGHT)
        head.setColorAt(1.0, BODY_DARK)
        painter.setBrush(head)
        painter.drawEllipse(QPointF(50.0, 37.0), 27.0, 22.0)
        cap = QPainterPath()
        cap.moveTo(23.2, 41.0)
        cap.cubicTo(20.0, 14.0, 80.0, 14.0, 76.8, 41.0)
        cap.cubicTo(70.0, 30.0, 63.0, 28.5, 50.0, 28.5)
        cap.cubicTo(37.0, 28.5, 30.0, 30.0, 23.2, 41.0)
        cap_fill = QLinearGradient(QPointF(50.0, 16.0), QPointF(50.0, 34.0))
        cap_fill.setColorAt(0.0, CAP_LIGHT)
        cap_fill.setColorAt(1.0, CAP_DARK)
        painter.setBrush(cap_fill)
        painter.drawPath(cap)

        self._paint_eyes(painter)

        # Мордочка, нос и улыбка.
        painter.setBrush(MUZZLE)
        painter.drawEllipse(QPointF(50.0, 48.0), 11.5, 8.0)
        nose = QPainterPath()
        nose.moveTo(45.5, 43.5)
        nose.quadTo(50.0, 41.5, 54.5, 43.5)
        nose.quadTo(52.5, 48.0, 50.0, 48.0)
        nose.quadTo(47.5, 48.0, 45.5, 43.5)
        painter.setBrush(INK)
        painter.drawPath(nose)
        pen = QPen(INK, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        mouth = QPainterPath()
        if self._mood == MOOD_SAD:
            mouth.moveTo(45.0, 53.5)
            mouth.quadTo(50.0, 50.0, 55.0, 53.5)
        else:
            mouth.moveTo(44.5, 50.5)
            mouth.quadTo(47.5, 54.0, 50.0, 50.5)
            mouth.quadTo(52.5, 54.0, 55.5, 50.5)
        painter.drawPath(mouth)
        painter.setPen(Qt.PenStyle.NoPen)

        # Передние лапы поверх тела.
        self._paint_paw(painter, QPointF(50.0 - SHOULDER_X, SHOULDER_Y), -left_paw)
        self._paint_paw(painter, QPointF(50.0 + SHOULDER_X, SHOULDER_Y), right_paw)

    def _paint_eyes(self, painter: QPainter) -> None:
        openness = self.eye_openness()
        look = self.look_offset()
        for ex in (40.0, 60.0):
            center = QPointF(ex, 37.5)
            if openness < 0.15:
                # Закрытый глаз — дужка. Радостный медоед жмурится дужкой вверх.
                pen = QPen(INK, 2.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                arc = QPainterPath()
                lift = -3.0 if self._gesture == MOOD_HAPPY else 2.0
                arc.moveTo(ex - 4.5, 38.0)
                arc.quadTo(ex, 38.0 + lift, ex + 4.5, 38.0)
                painter.drawPath(arc)
                painter.setPen(Qt.PenStyle.NoPen)
                continue
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(center, 5.0, 5.6 * openness)
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(center, 5.0, 5.6 * openness)
            painter.setClipPath(clip)
            pupil = QPointF(ex + 1.8 * look, 38.1)
            painter.setBrush(INK)
            painter.drawEllipse(pupil, 3.4, 3.8)
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(QPointF(pupil.x() - 1.1, pupil.y() - 1.4), 1.2, 1.2)
            painter.restore()

    @staticmethod
    def _paint_paw(painter: QPainter, shoulder: QPointF, angle: float) -> None:
        painter.save()
        painter.translate(shoulder)
        painter.rotate(angle)
        arm = QLinearGradient(QPointF(0.0, 0.0), QPointF(0.0, ARM_LENGTH + 6.0))
        arm.setColorAt(0.0, BODY_LIGHT)
        arm.setColorAt(1.0, BODY_DARK)
        painter.setBrush(arm)
        painter.drawRoundedRect(QRectF(-5.0, -3.0, 10.0, ARM_LENGTH + 6.0), 5.0, 5.0)
        painter.setBrush(PAW_DARK)
        painter.drawEllipse(QPointF(0.0, ARM_LENGTH + 1.5), 6.0, 5.2)
        # Коготки медоеда — его главная гордость.
        pen = QPen(CAP_LIGHT, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for claw in (-3.0, 0.0, 3.0):
            painter.drawLine(QPointF(claw, ARM_LENGTH + 5.0), QPointF(claw * 1.15, ARM_LENGTH + 8.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.restore()


__all__ = ["DrawnBadger"]
