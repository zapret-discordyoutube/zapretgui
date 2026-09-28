"""Надпись, которая выплывает при показе и потом живёт лёгким зацикленным бликом.

При каждом показе текст выплывает снизу и проявляется (с задержкой, чтобы
несколько строк шли одна за другой). Потом раз в несколько секунд по буквам
слева направо проходит светлая полоса.

Блик рисуется поверх обычного текста QLabel тем же drawText с кистью-градиентом,
поэтому попадает только в буквы: фон и соседние виджеты не подсвечиваются.
Между проходами надпись неподвижна и таймер спит. Анимация идёт, только пока
надпись видна, окно не свёрнуто и включены «Живые анимации».
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPixmap, QRegion
from PyQt6.QtWidgets import QLabel, QWidget

from ui.animation_policy import are_live_animations_enabled


ENTER_DURATION_MS = 700
ENTER_RISE_PX = 12.0
SWEEP_DURATION_MS = 1800
DEFAULT_PERIOD_MS = 7000
DEFAULT_FIRST_DELAY_MS = 900
_PEAK_ALPHA = 0.55


class ShimmerLabel(QLabel):
    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        period_ms: int = DEFAULT_PERIOD_MS,
        first_delay_ms: int = DEFAULT_FIRST_DELAY_MS,
        enter_delay_ms: int = 0,
        shimmer: bool = True,
        enter: bool = True,
    ) -> None:
        super().__init__(text, parent)
        self._period_ms = max(SWEEP_DURATION_MS + 500, int(period_ms))
        self._first_delay_ms = max(0, int(first_delay_ms))
        self._enter_delay_ms = max(0, int(enter_delay_ms))
        self._shimmer = bool(shimmer)
        self._enter_enabled = bool(enter)
        self._t = -1.0
        self._enter = 1.0
        self._glow = QColor(255, 255, 255)
        self._capturing = False
        self._text_pixmap: QPixmap | None = None
        self._text_pixmap_key = None

        self._enter_anim = QVariantAnimation(self)
        self._enter_anim.setStartValue(0.0)
        self._enter_anim.setEndValue(1.0)
        self._enter_anim.setDuration(ENTER_DURATION_MS)
        self._enter_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._enter_anim.valueChanged.connect(self._on_enter_value)
        self._enter_anim.finished.connect(self._on_enter_finished)

        self._enter_wait = QTimer(self)
        self._enter_wait.setSingleShot(True)
        self._enter_wait.timeout.connect(self._enter_anim.start)

        # QVariantAnimation, а не QPropertyAnimation: при выключенных анимациях
        # WinUI общий fallback подменяет QPropertyAnimation.start.
        self._sweep = QVariantAnimation(self)
        self._sweep.setStartValue(0.0)
        self._sweep.setEndValue(1.0)
        self._sweep.setDuration(SWEEP_DURATION_MS)
        self._sweep.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._sweep.valueChanged.connect(self._on_value)
        self._sweep.finished.connect(self._on_finished)

        self._pause = QTimer(self)
        self._pause.setSingleShot(True)
        self._pause.timeout.connect(self._begin_sweep)

    def set_glow_color(self, color: str | QColor) -> None:
        glow = QColor(color)
        if glow.isValid():
            self._glow = glow
            self.update()

    def is_sweeping(self) -> bool:
        return self._t >= 0.0

    def enter_progress(self) -> float:
        return self._enter

    # ---- цикл ----------------------------------------------------------

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _begin_sweep(self) -> None:
        if not self._can_animate():
            # Настройку могли включить позже — проверим на следующем круге.
            if self.isVisible():
                self._pause.start(self._period_ms)
            return
        self._refresh_snapshot()
        self._sweep.start()

    def _on_value(self, value) -> None:
        self._t = float(value)
        self.update()

    def _on_finished(self) -> None:
        self._t = -1.0
        self.update()
        if self.isVisible():
            self._pause.start(self._period_ms - SWEEP_DURATION_MS)

    def _on_enter_value(self, value) -> None:
        self._enter = float(value)
        self.update()

    def _on_enter_finished(self) -> None:
        self._enter = 1.0
        self.update()

    def _start_enter(self) -> None:
        self._enter_wait.stop()
        self._enter_anim.stop()
        if not self._enter_enabled or not self._can_animate():
            self._enter = 1.0
            return
        # Пока ждём своей очереди, строка скрыта — потом выплывает.
        self._refresh_snapshot()
        self._enter = 0.0
        self._enter_wait.start(self._enter_delay_ms)

    def _stop(self) -> None:
        self._pause.stop()
        self._sweep.stop()
        self._enter_wait.stop()
        self._enter_anim.stop()
        self._enter = 1.0
        if self._t >= 0.0:
            self._t = -1.0
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._start_enter()
        if (
            self._shimmer
            and not self._pause.isActive()
            and self._sweep.state() != QVariantAnimation.State.Running
        ):
            self._pause.start(self._enter_delay_ms + self._first_delay_ms)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._stop()
            elif self._shimmer and self.isVisible() and not self._pause.isActive():
                self._pause.start(self._first_delay_ms)

    # ---- отрисовка -----------------------------------------------------
    #
    # Текст берётся из родной отрисовки QLabel, снятой в прозрачную картинку:
    # отступы из QSS и курсив qfluentwidgets раскладывает по-своему, и простой
    # drawText уводил бы буквы на несколько пикселей.

    def _snapshot(self) -> QPixmap:
        ratio = self.devicePixelRatioF() or 1.0
        pixmap = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        self._capturing = True
        try:
            self.render(pixmap, QPoint(), QRegion(), QWidget.RenderFlag(0))
        finally:
            self._capturing = False
        return pixmap

    def _refresh_snapshot(self) -> None:
        """Снимает текст заново — вне paintEvent, перед выплыванием и каждым бликом."""
        self._text_pixmap = self._snapshot()
        self._text_pixmap_key = (self.size(), self.text())

    def _cached_snapshot(self) -> QPixmap | None:
        if self._text_pixmap_key != (self.size(), self.text()):
            return None
        return self._text_pixmap

    def _paint_entering(self) -> None:
        """Текст, пока выплывает: поднимается снизу вверх и проявляется."""
        progress = max(0.0, min(1.0, self._enter))
        text = self._cached_snapshot()
        if progress <= 0.0 or text is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setOpacity(progress)
            painter.drawPixmap(QPointF(0.0, ENTER_RISE_PX * (1.0 - progress)), text)
        finally:
            painter.end()

    def _paint_sheen(self, t: float) -> None:
        text = self._cached_snapshot()
        if text is None:
            return
        band = max(80.0, self.width() * 0.22)
        center = -band + (self.width() + 2.0 * band) * t
        peak = QColor(self._glow)
        peak.setAlphaF(_PEAK_ALPHA)
        clear = QColor(self._glow)
        clear.setAlphaF(0.0)
        gradient = QLinearGradient(QPointF(center - band / 2.0, 0.0), QPointF(center + band / 2.0, 0.0))
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(0.5, peak)
        gradient.setColorAt(1.0, clear)

        # Полоса света остаётся только там, где есть буквы.
        sheen = QPixmap(text.size())
        sheen.setDevicePixelRatio(text.devicePixelRatio())
        sheen.fill(Qt.GlobalColor.transparent)
        mask = QPainter(sheen)
        try:
            mask.drawPixmap(0, 0, text)
            mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            mask.fillRect(QRectF(0.0, 0.0, self.width(), self.height()), QBrush(gradient))
        finally:
            mask.end()

        painter = QPainter(self)
        try:
            painter.drawPixmap(0, 0, sheen)
        finally:
            painter.end()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._capturing:
            super().paintEvent(event)
            return
        if self._enter < 1.0:
            self._paint_entering()
            return
        super().paintEvent(event)
        if self._t >= 0.0 and self.text():
            self._paint_sheen(self._t)


__all__ = ["ShimmerLabel"]
