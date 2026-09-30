"""Талисман-медоед: значок программы с настроением.

Настроения:

- ``idle`` — сидит спокойно и изредка (раз в ~8 с) оглядывается;
- ``busy`` — работает: мелко покачивается и подпрыгивает, пока идёт проверка;
- ``happy`` — радостный прыжок с оборотом, потом спокойно сидит;
- ``sad`` — грустно наклоняет голову и немного оседает;
- ``alarm`` — встряхивается, потом сидит насторожённо (чуть наклонён).

По клику медоед делает оборот — просто так, для настроения.

Анимация идёт, только пока виджет виден, окно не свёрнуто и в настройках
включены «живые анимации». В покое таймеров нет, кроме редкого одиночного
таймера «оглядывания» в ``idle``.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from ui.accessibility import set_state_text
from ui.animation_policy import are_live_animations_enabled

MOOD_IDLE = "idle"
MOOD_BUSY = "busy"
MOOD_HAPPY = "happy"
MOOD_SAD = "sad"
MOOD_ALARM = "alarm"
MOODS = (MOOD_IDLE, MOOD_BUSY, MOOD_HAPPY, MOOD_SAD, MOOD_ALARM)

_MOOD_WORDS = {
    MOOD_IDLE: "ждёт",
    MOOD_BUSY: "работает",
    MOOD_HAPPY: "радуется",
    MOOD_SAD: "грустит",
    MOOD_ALARM: "насторожился",
}

# Длительности жестов (мс). ``busy`` — один цикл покачивания, он повторяется.
_GESTURE_MS = {
    MOOD_BUSY: 1400,
    MOOD_HAPPY: 1100,
    MOOD_SAD: 700,
    MOOD_ALARM: 650,
    "look": 900,
    "spin": 800,
}
IDLE_LOOK_INTERVAL_MS = 8000


def _ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


class Mascot(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None, *, size: int = 48, icon: QIcon | None = None) -> None:
        super().__init__(parent)
        side = max(16, int(size))
        self._side = side
        # Запас сверху на прыжок и по бокам на поворот.
        self.setFixedSize(int(side * 1.3), int(side * 1.45))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon = icon
        self._pixmap = QPixmap()
        self._mood = MOOD_IDLE
        self._gesture = ""
        self._t = 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)

        self._look_timer = QTimer(self)
        self._look_timer.setSingleShot(True)
        self._look_timer.timeout.connect(self._on_look_timer)
        set_state_text(self, "Талисман: ждёт")

    # --- настроение -----------------------------------------------------------

    def mood(self) -> str:
        return self._mood

    def gesture(self) -> str:
        return self._gesture

    def set_mood(self, mood: str) -> None:
        mood = mood if mood in MOODS else MOOD_IDLE
        changed = mood != self._mood
        self._mood = mood
        set_state_text(self, f"Талисман: {_MOOD_WORDS[mood]}")
        if mood == MOOD_IDLE:
            if self._gesture == MOOD_BUSY:
                self._stop_gesture()
            self._schedule_look()
            return
        self._look_timer.stop()
        if changed or mood == MOOD_HAPPY:
            self._play(mood)
        self.update()

    def spin(self) -> None:
        self._play("spin")

    def logo_top(self) -> int:
        """Где начинается сам значок в покое: выше — только запас под прыжок.

        Нужен соседним подписям, чтобы выравниваться по значку, а не по
        верхнему краю виджета.
        """
        return max(int(self.height() - 2 - self._side), 0)

    # --- анимация -------------------------------------------------------------

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _play(self, gesture: str) -> None:
        if not self._can_animate():
            self._gesture = ""
            self.update()
            return
        self._anim.stop()
        self._gesture = gesture
        self._t = 0.0
        self._anim.setDuration(_GESTURE_MS.get(gesture, 800))
        # Работа идёт, пока не скажут иначе: покачивание повторяется.
        self._anim.setLoopCount(-1 if gesture == MOOD_BUSY else 1)
        self._anim.start()

    def _stop_gesture(self) -> None:
        self._anim.stop()
        self._gesture = ""
        self._t = 0.0
        self.update()

    def _schedule_look(self) -> None:
        self._look_timer.stop()
        if self._mood == MOOD_IDLE and self._can_animate():
            self._look_timer.start(IDLE_LOOK_INTERVAL_MS)

    def _on_look_timer(self) -> None:
        if self._mood == MOOD_IDLE and self._anim.state() != QVariantAnimation.State.Running:
            self._play("look")
        self._schedule_look()

    def _on_value(self, value) -> None:
        try:
            self._t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_finished(self) -> None:
        self._gesture = ""
        self._t = 0.0
        self.update()
        if self._mood == MOOD_IDLE:
            self._schedule_look()

    # --- события ---------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.spin()
            self.clicked.emit()
        super().mousePressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._mood == MOOD_BUSY:
            self._play(MOOD_BUSY)
        self._schedule_look()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._look_timer.stop()
        self._stop_gesture()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._look_timer.stop()
                self._stop_gesture()
            elif self._mood == MOOD_BUSY:
                self._play(MOOD_BUSY)

    # --- отрисовка ---------------------------------------------------------------

    def pose(self) -> tuple[float, float, float, float]:
        """(сдвиг по y в долях размера, поворот в градусах, масштаб x, масштаб y)."""
        t = self._t
        g = self._gesture
        if g == MOOD_BUSY:
            # Покачивание с двумя маленькими подскоками за цикл.
            hop = -0.06 * abs(math.sin(2 * math.pi * t))
            tilt = 7.0 * math.sin(2 * math.pi * t)
            squash = 1.0 + 0.04 * math.sin(4 * math.pi * t)
            return hop, tilt, 2.0 - squash, squash
        if g == MOOD_HAPPY:
            # Присесть, прыжок с оборотом, мягкое приземление.
            if t < 0.15:
                k = t / 0.15
                return 0.04 * k, 0.0, 1.0 + 0.1 * k, 1.0 - 0.12 * k
            if t < 0.75:
                k = (t - 0.15) / 0.6
                return -0.32 * math.sin(math.pi * k), 360.0 * _ease_out_back(k), 1.0, 1.0
            k = (t - 0.75) / 0.25
            return 0.0, 0.0, 1.0 + 0.08 * math.sin(math.pi * k), 1.0 - 0.08 * math.sin(math.pi * k)
        if g == MOOD_ALARM:
            shake = 12.0 * math.sin(6 * math.pi * t) * (1.0 - t)
            return 0.0, shake, 1.0, 1.0
        if g == "look":
            return 0.0, 10.0 * math.sin(2 * math.pi * t) * (1.0 - t * 0.5), 1.0, 1.0
        if g == "spin":
            return -0.08 * math.sin(math.pi * t), 360.0 * _ease_out_back(t), 1.0, 1.0
        # Покой: у грустного и насторожённого медоеда своя поза.
        if self._mood == MOOD_SAD:
            k = 1.0 if g != MOOD_SAD else t
            return 0.05 * k, -14.0 * k, 1.0, 1.0 - 0.06 * k
        if self._mood == MOOD_ALARM:
            return 0.0, 6.0, 1.0, 1.0
        return 0.0, 0.0, 1.0, 1.0

    def _current_pixmap(self) -> QPixmap:
        if self._pixmap.isNull():
            icon = self._icon or QApplication.windowIcon()
            if icon is not None and not icon.isNull():
                ratio = self.devicePixelRatioF() or 1.0
                self._pixmap = icon.pixmap(int(self._side * ratio), int(self._side * ratio))
                self._pixmap.setDevicePixelRatio(ratio)
        return self._pixmap

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        pixmap = self._current_pixmap()
        if pixmap.isNull():
            return
        dy, angle, sx, sy = self.pose()
        side = float(self._side)
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        # Опорная точка — низ значка: медоед приседает и прыгает от «пола».
        base = QPointF(self.width() / 2, self.height() - 2)
        painter.translate(base.x(), base.y() + dy * side)
        painter.scale(sx, sy)
        painter.translate(0, -side / 2)
        painter.rotate(angle)
        painter.drawPixmap(QRectF(-side / 2, -side / 2, side, side), pixmap, QRectF(pixmap.rect()))
        painter.end()


__all__ = ["MOODS", "MOOD_ALARM", "MOOD_BUSY", "MOOD_HAPPY", "MOOD_IDLE", "MOOD_SAD", "Mascot"]
