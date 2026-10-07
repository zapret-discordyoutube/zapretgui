"""Карточка «Статус работы» с живым фоном.

Фон карточки мягко подкрашен цветом состояния (зелёный — работает,
оранжевый — запуск, красный — остановлен) и светлеет к правому краю.
Когда Zapret включился, от кнопки по карточке расходится волна.

Пока Zapret работает, свечение живое: за сценой навстречу друг другу плавно
ходят два пятна света разных оттенков (зелёный уходит в бирюзовый и в
салатовый), и там, где они сходятся, цвет переливается. Своего таймера на
это нет — карточка рисует по кадрам сцены, которые и так идут, и только под
сценой, где фон всё равно перерисовывается. Сами пятна нарисованы один раз
в готовые картинки, кадр — это два наложения картинки.

Цвет и волна — короткие анимации по событию. Без работающего обхода, при
скрытой странице и при выключенных «живых анимациях» карточка кадров не рисует.

Свечение меняется каждый кадр, а рамка, фон и подкраска под ним — нет. Но
перерисовывать под свечением приходится всё, и рамка со сглаженными углами
съедала половину времени кадра. Поэтому неподвижная часть карточки хранится
готовым слоем (``ui.paint_layer_cache``): кадр — это слой и два пятна света.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QBoxLayout

from qfluentwidgets import CardWidget, isDarkTheme

from ui.animation_policy import are_live_animations_enabled
from ui.paint_layer_cache import LayerCache


TINT_FADE_MS = 420
# Уже этого сцена встаёт отдельной строкой над текстом, иначе текст наезжает на неё.
STACK_BELOW_WIDTH = 760
WAVE_MS = 900
# Насколько заметна подкраска у левого края (у сцены) в тёмной и светлой теме.
TINT_ALPHA_DARK = 0.17
TINT_ALPHA_LIGHT = 0.13
WAVE_ALPHA = 0.26
# Живое свечение при работающем обходе: два пятна света, каждое со своим
# оттенком, периодом хода туда-обратно и периодом разгорания (секунды).
SHIMMER_HUE_SWING = 34
SHIMMER_SPOTS = (
    # (сдвиг оттенка, период хода, период разгорания, откуда, куда — в долях ширины)
    (SHIMMER_HUE_SWING, 7.0, 3.1, 0.34, 0.66),
    (-SHIMMER_HUE_SWING, 9.5, 4.3, 0.66, 0.34),
)
# Радиус пятна в долях ширины области свечения: у её правого края пятно уже гаснет.
SHIMMER_RADIUS = 0.34
# Запас справа от сцены, который тоже захватывает свечение.
SHIMMER_PAD = 26
_SPOT_CACHE: dict[tuple[str, int, float], QPixmap] = {}


def _spot_pixmap(color: QColor, radius: int, ratio: float) -> QPixmap:
    """Круглое пятно света, гаснущее к краю. Рисуется один раз на цвет и размер."""
    key = (color.name(), radius, ratio)
    pixmap = _SPOT_CACHE.get(key)
    if pixmap is None:
        side = int(radius * 2 * ratio)
        pixmap = QPixmap(side, side)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        clear = QColor(color)
        clear.setAlphaF(0.0)
        gradient = QRadialGradient(QPointF(radius, radius), radius)
        gradient.setColorAt(0.0, color)
        gradient.setColorAt(0.55, QColor(color.red(), color.green(), color.blue(), 110))
        gradient.setColorAt(1.0, clear)
        painter = QPainter(pixmap)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(QRectF(0, 0, radius * 2, radius * 2))
        painter.end()
        if len(_SPOT_CACHE) > 12:
            _SPOT_CACHE.clear()
        _SPOT_CACHE[key] = pixmap
    return pixmap


def _shift_hue(color: QColor, degrees: float) -> QColor:
    hue, saturation, value, _alpha = color.getHsv()
    if hue < 0:
        return QColor(color)
    return QColor.fromHsv(int(hue + degrees) % 360, saturation, value)


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
        self._stacking_layout = None
        self._phase = ""
        # None — свечение неподвижно; иначе время потока сцены в секундах.
        self._shimmer_t: float | None = None
        # Готовый слой рамки, фона и подкраски (обычный вид и вид под мышью).
        self._layers = LayerCache(capacity=3)

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

    def set_stacking_layout(self, layout) -> None:
        """Раскладка карточки, которую в узком окне надо развернуть сверху вниз."""
        self._stacking_layout = layout
        self._sync_stacking()

    def is_stacked(self) -> bool:
        layout = self._stacking_layout
        return layout is not None and layout.direction() == QBoxLayout.Direction.TopToBottom

    def _sync_stacking(self) -> None:
        layout = self._stacking_layout
        if layout is None:
            return
        stacked = self.width() < STACK_BELOW_WIDTH
        if stacked == self.is_stacked():
            return
        layout.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        layout.setSpacing(10 if stacked else 18)
        scene = self._scene
        if scene is not None:
            # Отдельной строкой сцена растягивается на всю ширину: дорожки длиннее.
            scene.set_stretched(stacked)
            layout.setAlignment(scene, Qt.AlignmentFlag(0) if stacked else Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_stacking()

    def bind_scene(self, scene) -> None:
        """Связывает карточку со сценой: цвет фона и волна от кнопки."""
        self._scene = scene
        scene.colorChanged.connect(self.set_tint)
        scene.phaseChanged.connect(self._on_scene_phase_changed)
        scene.flowFrame.connect(self._on_scene_flow_frame)
        self._sync_stacking()

    def shimmer_rect(self) -> QRect:
        """Область за сценой, где живёт свечение: только её и перерисовываем."""
        scene = self._scene
        right = scene.geometry().right() + SHIMMER_PAD if scene is not None else 0
        return QRect(0, 0, max(0, min(self.width(), right)), self.height())

    def is_shimmering(self) -> bool:
        return self._shimmer_t is not None

    def _on_scene_flow_frame(self, flow_time: float) -> None:
        self._shimmer_t = float(flow_time)
        self.update(self.shimmer_rect())

    def _stop_shimmer(self) -> None:
        if self._shimmer_t is not None:
            self._shimmer_t = None
            self.update()

    def _on_scene_phase_changed(self, phase: str) -> None:
        previous, self._phase = self._phase, phase
        if phase != "running":
            self._stop_shimmer()
        scene = self._scene
        if scene is not None and phase == "running" and previous and previous != "running":
            self.play_wave(scene.mapTo(self, scene.gate_center()), scene.target_color().name())

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
        self._shimmer_t = None
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._wave.stop()
                self._wave_t = 0.0

    def _base_key(self) -> tuple:
        """Всё, от чего зависит неподвижная часть карточки."""
        return (
            self.width(),
            self.height(),
            float(self.borderRadius),
            isDarkTheme(),
            bool(self.isHover),
            bool(self.isPressed),
            self.backgroundColor.rgba(),
            self._tint.rgba(),
        )

    def _paint_base(self, painter: QPainter) -> None:
        """Рамка и фон карточки и подкраска цветом состояния поверх них."""
        self._paint_card_frame(painter)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = float(self.borderRadius)
        strength = TINT_ALPHA_DARK if isDarkTheme() else TINT_ALPHA_LIGHT
        near = QColor(self._tint)
        near.setAlphaF(strength)
        far = QColor(self._tint)
        far.setAlphaF(strength * 0.12)
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        gradient.setColorAt(0.0, near)
        gradient.setColorAt(0.55, far)
        gradient.setColorAt(1.0, far)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        # Скруглённая фигура вместо обрезки по контуру: обрезка со сглаживанием
        # в несколько раз дороже, а кадры свечения идут постоянно.
        painter.drawRoundedRect(rect, radius, radius)

    def _paint_card_frame(self, painter: QPainter) -> None:
        """Рамка и фон обычной карточки — то же, что рисует ``CardWidget.paintEvent``.

        Библиотека рисует их только прямо на виджете, а в готовый слой нужно
        нарисовать своим ``painter``, поэтому рисунок повторён здесь. За
        совпадением с библиотекой следит тест
        ``test_card_frame_matches_the_library_card`` (tests/test_control_animations.py).
        """
        w, h = self.width(), self.height()
        r = self.borderRadius
        d = 2 * r
        dark = isDarkTheme()

        top = QPainterPath()
        top.arcMoveTo(1, h - d - 1, d, d, 240)
        top.arcTo(1, h - d - 1, d, d, 225, -60)
        top.lineTo(1, r)
        top.arcTo(1, 1, d, d, -180, -90)
        top.lineTo(w - r, 1)
        top.arcTo(w - d - 1, 1, d, d, 90, -90)
        top.lineTo(w - 1, h - r)
        top.arcTo(w - d - 1, h - d - 1, d, d, 0, -60)
        top_color = QColor(0, 0, 0, 20)
        if dark:
            if self.isPressed:
                top_color = QColor(255, 255, 255, 18)
            elif self.isHover:
                top_color = QColor(255, 255, 255, 13)
        else:
            top_color = QColor(0, 0, 0, 15)
        painter.strokePath(top, top_color)

        bottom = QPainterPath()
        bottom.arcMoveTo(1, h - d - 1, d, d, 240)
        bottom.arcTo(1, h - d - 1, d, d, 240, 30)
        bottom.lineTo(w - r - 1, h - 1)
        bottom.arcTo(w - d - 1, h - d - 1, d, d, 270, 30)
        bottom_color = top_color
        if not dark and self.isHover and not self.isPressed:
            bottom_color = QColor(0, 0, 0, 27)
        painter.strokePath(bottom, bottom_color)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.backgroundColor)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), r, r)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._tint.isValid():
            super().paintEvent(event)
            return
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = float(self.borderRadius)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if self._tint_fade.state() == QVariantAnimation.State.Running:
            # Цвет перетекает: подкраска каждый кадр другая, слой не годится.
            self._paint_base(painter)
        else:
            self._layers.draw(painter, self._base_key(), QRectF(self.rect()), self._paint_base)

        strength = TINT_ALPHA_DARK if isDarkTheme() else TINT_ALPHA_LIGHT
        shimmer_t = self._shimmer_t
        area = self.shimmer_rect()
        if shimmer_t is not None and area.width() > 0:
            spot_radius = max(8, int(area.width() * SHIMMER_RADIUS))
            ratio = self.devicePixelRatioF() or 1.0
            center_y = rect.center().y()
            # Пятна гаснут раньше, чем доходят до скруглённых углов: хватает
            # дешёвой обрезки по прямоугольнику.
            painter.setClipRect(rect)
            for hue, drift_s, pulse_s, start, end in SHIMMER_SPOTS:
                # Пятно плавно ходит между двумя точками и слегка разгорается.
                drift = 0.5 - 0.5 * math.cos(2 * math.pi * shimmer_t / drift_s)
                pulse = 0.72 + 0.28 * math.sin(2 * math.pi * shimmer_t / pulse_s)
                center_x = area.width() * (start + (end - start) * drift)
                painter.setOpacity(min(1.0, strength * 2.1 * pulse))
                painter.drawPixmap(
                    QPointF(center_x - spot_radius, center_y - spot_radius),
                    _spot_pixmap(_shift_hue(QColor(self._tint), hue), spot_radius, ratio),
                )
            painter.setOpacity(1.0)

        if self._wave_t > 0.0:
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            painter.setClipPath(clip)
            wave = QColor(self._wave_color)
            wave.setAlphaF(WAVE_ALPHA * (1.0 - self._wave_t) ** 1.5)
            painter.setBrush(wave)
            reach = max(self._wave_origin.x(), rect.width() - self._wave_origin.x()) + rect.height()
            radius_now = 18.0 + reach * self._wave_t
            painter.drawEllipse(self._wave_origin, radius_now, radius_now)
        painter.end()


__all__ = ["StatusHeroCard"]
