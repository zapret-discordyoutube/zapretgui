"""Лёгкое проявление частей рисующего виджета: плитки, строки и значки появляются по очереди."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QTimer, QVariantAnimation
from PyQt6.QtWidgets import QWidget

from ui.animation_policy import are_live_animations_enabled

REVEAL_MS = 420
# Какую долю времени занимает разбег между первой и последней частью.
STAGGER = 0.6
# На сколько точек часть приподнимается, пока проявляется.
RISE_PX = 4.0
# Как часто проверять, приземлился ли летящий блок, и сколько раз самое большее.
_WAIT_STEP_MS = 50
_MAX_WAITS = 30


class Reveal(QObject):
    """Один таймер на весь виджет: тот спрашивает долю появления каждой своей части.

    Виджет рисует части сам (плитки, строки таблицы), поэтому анимировать их
    по отдельности нечем — и незачем: одна анимация перерисовывает один виджет
    меньше полсекунды и останавливается. Играет один раз, при первом показе.
    """

    def __init__(self, widget: QWidget, *, duration_ms: int = REVEAL_MS) -> None:
        super().__init__(widget)
        self._widget = widget
        self._value = 1.0
        self._played = False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(int(duration_ms))
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_value)
        self._waited = 0
        self._wait = QTimer(self)
        self._wait.setSingleShot(True)
        self._wait.timeout.connect(self._start_when_landed)
        widget.installEventFilter(self)

    def value(self) -> float:
        return self._value

    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running or self._wait.isActive()

    def play(self) -> None:
        self._played = True
        self._anim.stop()
        self._wait.stop()
        if not are_live_animations_enabled():
            self._value = 1.0
            self._widget.update()
            return
        self._value = 0.0
        self._widget.update()
        # Блок, в котором стоит виджет, сам может «выплывать» картинкой: пока он летит,
        # части ждут невидимыми и проявляются, когда блок встал на место.
        self._waited = 0
        self._wait.start(0)

    def _start_when_landed(self) -> None:
        self._waited += 1
        if self._flying() and self._waited < _MAX_WAITS:
            self._wait.start(_WAIT_STEP_MS)
            return
        self._anim.start()

    def _flying(self) -> bool:
        from ui.widgets.stagger_float_in import is_floating_in

        widget = self._widget
        while widget is not None:
            if is_floating_in(widget):
                return True
            widget = widget.parentWidget()
        return False

    def finish(self) -> None:
        self._anim.stop()
        self._wait.stop()
        self._value = 1.0

    def part(self, index: int, count: int) -> float:
        """Доля появления части ``index`` из ``count``: 0 — ещё не видна, 1 — стоит на месте."""
        if self._value >= 1.0 or count <= 0:
            return 1.0
        start = STAGGER * index / (count - 1) if count > 1 else 0.0
        return max(0.0, min(1.0, (self._value - start) / (1.0 - STAGGER)))

    def _on_value(self, value) -> None:
        self._value = float(value)
        self._widget.update()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._widget:
            if event.type() == QEvent.Type.Show and not self._played:
                self.play()
            elif event.type() == QEvent.Type.Hide:
                # Спрятанный виджет не доигрывает: вернётся на экран уже готовым.
                self.finish()
        return False


__all__ = ["REVEAL_MS", "RISE_PX", "Reveal"]
