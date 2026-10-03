"""Карточка «Статус работы» с живым фоном.

Фон карточки мягко подкрашен цветом состояния (зелёный — работает,
оранжевый — запуск, красный — остановлен) и светлеет к правому краю.
Когда Zapret включился, от кнопки по карточке расходится волна.

Цвет и волна — короткие анимации по событию: в покое карточка не рисует
кадров.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath

from qfluentwidgets import CardWidget, isDarkTheme

from ui.animation_policy import are_live_animations_enabled
from ui.widgets.fun.mascot import MOOD_ALARM, MOOD_BUSY, MOOD_HAPPY, MOOD_IDLE, MOOD_SAD


TINT_FADE_MS = 420
WAVE_MS = 900
# Насколько заметна подкраска у левого края (у сцены) в тёмной и светлой теме.
TINT_ALPHA_DARK = 0.17
TINT_ALPHA_LIGHT = 0.13
WAVE_ALPHA = 0.26
# Уже этого талисман не помещается рядом с текстом и кнопкой «Закрыть программу».
MASCOT_MIN_CARD_WIDTH = 720


def mascot_mood_for_phase(phase: str, previous: str = "") -> str:
    """Настроение талисмана по состоянию Zapret."""
    if phase == "running":
        # Радуется, когда обход включился на глазах; при первом показе просто сидит.
        return MOOD_HAPPY if previous and previous != "running" else MOOD_IDLE
    if phase in {"autostart_pending", "starting", "stopping"}:
        return MOOD_BUSY
    if phase == "failed":
        return MOOD_SAD
    return MOOD_ALARM


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class StatusHeroCard(CardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tint = QColor()
        self._tint_from = QColor()
        self._tint_to = QColor()
        self._wave_t = 0.0
        self._wave_origin = QPointF()
        self._wave_color = QColor()
        self._scene = None
        self._mascot = None
        self._phase = ""

        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях WinUI общий fallback подменяет QPropertyAnimation.start.
        self._tint_fade = QVariantAnimation(self)
        self._tint_fade.setStartValue(0.0)
        self._tint_fade.setEndValue(1.0)
        self._tint_fade.setDuration(TINT_FADE_MS)
        self._tint_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._tint_fade.valueChanged.connect(self._on_tint_value)

        self._wave = QVariantAnimation(self)
        self._wave.setStartValue(0.0)
        self._wave.setEndValue(1.0)
        self._wave.setDuration(WAVE_MS)
        self._wave.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._wave.valueChanged.connect(self._on_wave_value)
        self._wave.finished.connect(self._on_wave_finished)

    # Карточка ничего не делает по клику, поэтому не подсвечивается под мышью.
    def _hoverBackgroundColor(self):  # noqa: N802
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self):  # noqa: N802
        return self._normalBackgroundColor()

    def bind_scene(self, scene, mascot) -> None:
        """Связывает карточку со сценой: цвет фона, волна и настроение талисмана."""
        self._scene = scene
        self._mascot = mascot
        scene.colorChanged.connect(self.set_tint)
        scene.phaseChanged.connect(self._on_scene_phase_changed)
        self._sync_mascot()

    def _on_scene_phase_changed(self, phase: str) -> None:
        previous, self._phase = self._phase, phase
        mascot = self._mascot
        if mascot is not None:
            mascot.set_mood(mascot_mood_for_phase(phase, previous))
        scene = self._scene
        if scene is not None and phase == "running" and previous and previous != "running":
            self.play_wave(scene.mapTo(self, scene.gate_center()), scene.target_color().name())

    def _sync_mascot(self) -> None:
        # Талисман прячется, когда карточка узкая.
        mascot = self._mascot
        if mascot is None:
            return
        wanted = self.width() >= MASCOT_MIN_CARD_WIDTH
        if mascot.isHidden() == wanted:
            mascot.setVisible(wanted)

    def tint(self) -> QColor:
        return QColor(self._tint)

    def set_tint(self, color: str) -> None:
        target = QColor(color)
        if not target.isValid() or target == self._tint_to:
            return
        self._tint_to = target
        self._tint_fade.stop()
        if self._tint.isValid() and self.isVisible() and are_live_animations_enabled():
            self._tint_from = QColor(self._tint)
            self._tint_fade.start()
        else:
            self._tint = QColor(target)
            self.update()

    def play_wave(self, origin: QPoint, color: str) -> None:
        """Волна от кнопки: Zapret только что включился."""
        if not self.isVisible() or not are_live_animations_enabled():
            return
        window = self.window()
        if window is not None and window.isMinimized():
            return
        self._wave_origin = QPointF(origin)
        self._wave_color = QColor(color)
        self._wave.stop()
        self._wave_t = 0.0
        self._wave.start()

    def is_wave_playing(self) -> bool:
        return self._wave.state() == QVariantAnimation.State.Running

    def _on_tint_value(self, value) -> None:
        try:
            t = float(value)
        except (TypeError, ValueError):
            return
        self._tint = _mix(self._tint_from, self._tint_to, t)
        self.update()

    def _on_wave_value(self, value) -> None:
        try:
            self._wave_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def _on_wave_finished(self) -> None:
        self._wave_t = 0.0
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._tint_fade.stop()
        if self._tint_to.isValid():
            self._tint = QColor(self._tint_to)
        self._wave.stop()
        self._wave_t = 0.0
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_mascot()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._wave.stop()
                self._wave_t = 0.0

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._tint.isValid():
            return
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = float(self.borderRadius)
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setClipPath(clip)

        strength = TINT_ALPHA_DARK if isDarkTheme() else TINT_ALPHA_LIGHT
        near = QColor(self._tint)
        near.setAlphaF(strength)
        far = QColor(self._tint)
        far.setAlphaF(strength * 0.12)
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        gradient.setColorAt(0.0, near)
        gradient.setColorAt(0.55, far)
        gradient.setColorAt(1.0, far)
        painter.setBrush(gradient)
        painter.drawRect(rect)

        if self._wave_t > 0.0:
            wave = QColor(self._wave_color)
            wave.setAlphaF(WAVE_ALPHA * (1.0 - self._wave_t) ** 1.5)
            painter.setBrush(wave)
            reach = max(self._wave_origin.x(), rect.width() - self._wave_origin.x()) + rect.height()
            radius_now = 18.0 + reach * self._wave_t
            painter.drawEllipse(self._wave_origin, radius_now, radius_now)
        painter.end()


__all__ = ["MASCOT_MIN_CARD_WIDTH", "StatusHeroCard", "mascot_mood_for_phase"]
