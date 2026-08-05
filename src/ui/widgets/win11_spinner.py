"""Windows 11 style spinner based on the stock Fluent progress ring."""

from PyQt6.QtCore import QAbstractAnimation
from PyQt6.QtGui import QColor
from qfluentwidgets import IndeterminateProgressRing

from ui.theme import get_theme_tokens


class Win11Spinner(IndeterminateProgressRing):
    """Кольцо ожидания с постоянно видимой вращающейся дугой."""

    def __init__(self, size=20, color=None, parent=None):
        super().__init__(parent=parent, start=False)
        self._size = max(12, int(size))
        self._running_requested = False
        self.setFixedSize(self._size, self._size)
        self.setStrokeWidth(2)
        if color is None:
            try:
                color = get_theme_tokens().accent_hex
            except Exception:
                color = "#5caee8"
        self._color = QColor(color)
        self.setCustomBarColor(self._color, self._color)
        self.setCustomBackgroundColor(
            QColor(0, 0, 0, 30),
            QColor(255, 255, 255, 30),
        )
        for animation in (self.spanAngleAni1, self.spanAngleAni2):
            animation.setStartValue(90)
            animation.setEndValue(90)

    def start(self):
        """Запускает анимацию"""
        self._running_requested = True
        self.show()
        self._start_animation()

    def stop(self):
        """Останавливает анимацию"""
        self._running_requested = False
        self._stop_animation()
        self.hide()

    def _animation_is_running(self) -> bool:
        return self.aniGroup.state() == QAbstractAnimation.State.Running

    def _start_animation(self) -> None:
        if not self._running_requested or self._animation_is_running():
            return
        super().start()
        self.spanAngle = 90

    def _stop_animation(self) -> None:
        if self.aniGroup.state() != QAbstractAnimation.State.Stopped:
            super().stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._start_animation()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
