"""Глобус, который будто поворачивается: плавно сменяет материки и чуть парит.

Раз в несколько секунд значок мягко перетекает в следующий вид Земли
(Америка → Европа → Африка → Азия), а между сменами едва заметно
поднимается и опускается. Анимация идёт, только пока глобус виден, окно
не свёрнуто и включены «Живые анимации»; иначе показан один материк.

Кадры глобус берёт у общего такта (``ui.frame_clock``), а не у своих
таймеров: при заблокированном сеансе и выключенном экране такт стоит, и
глобус не рисует кадры в пустоту; его перерисовка сливается с остальными
живыми анимациями окна в одну. Картинка считается по времени, а не по числу
кадров, поэтому от частоты кадров не зависит.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QEvent, QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled
from ui.frame_clock import frame_clock


GLOBE_ICONS = ("fa5s.globe-americas", "fa5s.globe-europe", "fa5s.globe-africa", "fa5s.globe-asia")
TURN_PERIOD_MS = 3200
TURN_DURATION_MS = 900
# Один подъём и спуск. Столько он и длился на Windows: свой таймер на 33 мс
# система отдавала раз в ~47 мс, и задуманные 3,2 с растягивались до 4,5 с.
FLOAT_PERIOD_MS = 4500
FLOAT_PX = 2.5
# Парение — 20 кадров в секунду (за кадр глобус сдвигается меньше чем на
# пятую часть точки), смена материка — 30: плавному перетеканию нужно чаще.
_FLOAT_FRAME_MS = 50
_TURN_FRAME_MS = 33


class TurningGlobe(QWidget):
    def __init__(self, parent=None, *, size: int = 48, color: str = "#60cdff") -> None:
        super().__init__(parent)
        self._side = max(16, int(size))
        self._color = str(color)
        self._index = 0
        self._blend = 0.0
        self._float_phase = 0.0
        # Глобус перерисовывается ~30 раз в секунду — значки держим готовыми.
        self._icons: dict[tuple[str, str], object] = {}
        self.setFixedSize(self._side + 8, self._side + 8)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._turn_curve = QEasingCurve(QEasingCurve.Type.InOutSine)
        # Материк, с которого начался текущий запуск анимации.
        self._base_index = 0
        self._frames = frame_clock().subscribe(self._on_frame, interval_ms=_FLOAT_FRAME_MS, owner=self)

    def set_color(self, color: str) -> None:
        self._color = str(color)
        self._icons.clear()
        self.update()

    def _icon(self, name: str):
        key = (name, self._color)
        icon = self._icons.get(key)
        if icon is None:
            from ui.theme import get_themed_qta_icon

            icon = get_themed_qta_icon(name, color=self._color)
            self._icons[key] = icon
        return icon

    def current_icon(self) -> str:
        return GLOBE_ICONS[self._index]

    def is_animating(self) -> bool:
        return self._frames.isActive()

    # ---- цикл ----------------------------------------------------------

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _start(self) -> None:
        if not self._can_animate() or self._frames.isActive():
            return
        self._base_index = self._index
        self._frames.setInterval(_FLOAT_FRAME_MS)
        self._frames.start()

    def _stop(self) -> None:
        self._frames.stop()
        self._blend = 0.0
        self._float_phase = 0.0
        self.update()

    def _on_frame(self) -> None:
        if not self._can_animate():
            self._stop()
            return
        elapsed = self._frames.elapsed_ms()
        # Круг: материк стоит, затем перетекает в следующий.
        period = max(1, int(TURN_PERIOD_MS))
        duration = min(period, max(1, int(TURN_DURATION_MS)))
        turns, within = divmod(elapsed, period)
        turning_for = within - (period - duration)
        self._index = (self._base_index + int(turns)) % len(GLOBE_ICONS)
        self._blend = self._turn_curve.valueForProgress(turning_for / duration) if turning_for > 0 else 0.0
        self._float_phase = (elapsed / FLOAT_PERIOD_MS) % 1.0
        self._frames.setInterval(_TURN_FRAME_MS if turning_for > 0 else _FLOAT_FRAME_MS)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._stop()
            else:
                self._start()

    # ---- отрисовка -----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        _ = event
        dy = -FLOAT_PX * math.sin(2.0 * math.pi * self._float_phase)
        left = (self.width() - self._side) / 2.0
        top = (self.height() - self._side) / 2.0 + dy
        rect = QRectF(left, top, self._side, self._side).toRect()

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            current = self._icon(GLOBE_ICONS[self._index])
            if self._blend <= 0.0:
                current.paint(painter, rect)
                return
            following = self._icon(GLOBE_ICONS[(self._index + 1) % len(GLOBE_ICONS)])
            painter.setOpacity(1.0 - self._blend)
            current.paint(painter, rect)
            painter.setOpacity(self._blend)
            following.paint(painter, rect)
        finally:
            painter.end()


__all__ = ["GLOBE_ICONS", "TurningGlobe"]
