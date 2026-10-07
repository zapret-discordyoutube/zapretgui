"""Метка «● Работает / ● Остановлен» в верхней панели окна.

Метка видна в любом разделе и сама является выключателем: клик запускает
или останавливает Zapret через единый пульт ui.launch_control.LaunchControl.
Состояние она не вычисляет — фаза приходит из общего UI-store через
ui/window_state_binder.py.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy
from qfluentwidgets import TransparentPushButton, setCustomStyleSheet

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip
from ui.frame_clock import frame_clock
from ui.launch_control import BUSY_LAUNCH_PHASES, mode_label_for_launch_method, normalize_launch_phase, phase_color
from ui.paint_layer_cache import LayerCache
from ui.theme import get_theme_tokens, to_qcolor
from ui.theme_semantic import get_semantic_palette
from ui.title_badge_paint import badge_qss, badge_shape, current_theme_name


LAUNCH_TITLE_BADGE_OBJECT_NAME = "launchTitleBadge"
BADGE_DOT_RADIUS = 3.5
BADGE_DOT_LEFT = 12
BADGE_TEXT_LEFT = 23
# Пока идёт запуск или остановка, точка мягко «дышит».
BUSY_BREATH_MS = 1200
# «Работает»: от точки расходится кольцо, подложка в такт светлеет.
RUNNING_PULSE_MS = 1800
# Кольцо дорастает до 10 px: целиком помещается в значок высотой 22 px.
RUNNING_RING_GROWTH = 5.5
# Кадры значка идут от общего такта (ui.frame_clock) с той же частотой, что у
# точки статуса на главной: 30 в секунду. За кадр кольцо сдвигается не больше
# чем на 0,2 px, его прозрачность меняется примерно на 6/255, свечение
# подложки — на 3/255. Раньше значок рисовался своим таймером 62 раза в
# секунду, отдельно от остальных анимаций, и при работающем Zapret был главной
# нагрузкой программы в покое: каждый кадр — перерисовка заголовка и отправка
# окна на экран.
BADGE_FRAME_MS = 33
# Фаза кольца идёт шагами в один кадр экрана (1/60 с): мельче экран всё равно
# не покажет. За полный круг шагов конечное число, и на каждом круге они те же,
# поэтому кольцо, ореол и точку для каждого шага можно нарисовать один раз
# (ui.paint_layer_cache) и дальше накладывать готовой картинкой.
PULSE_STEPS = round(RUNNING_PULSE_MS * 60 / 1000)
# Ширина области слева, в которую помещается кольцо вокруг точки.
PULSE_LAYER_WIDTH = 24.0
STOPPED_DOT_COLOR = "#9aa0a6"


@dataclass(frozen=True, slots=True)
class LaunchBadgeView:
    phase: str
    text: str
    tooltip: str


def build_launch_badge_view(*, phase: str, launch_method: str, language: str | None) -> LaunchBadgeView:
    normalized = normalize_launch_phase(phase)
    if normalized == "autostart_pending":
        key = "starting"
    else:
        key = normalized
    defaults = {
        "running": ("Работает", "{mode} работает · нажмите, чтобы остановить"),
        "starting": ("Запуск…", "{mode} запускается · нажмите, чтобы остановить"),
        "stopping": ("Остановка…", "{mode} останавливается…"),
        "stopped": ("Остановлен", "{mode} остановлен · нажмите, чтобы запустить"),
        "failed": ("Ошибка", "Ошибка запуска {mode} · нажмите, чтобы попробовать снова"),
    }
    text_default, tooltip_default = defaults[key]
    mode = mode_label_for_launch_method(launch_method)
    text = tr_catalog(f"launch.badge.{key}", language=language, default=text_default)
    tooltip = tr_catalog(f"launch.badge.tooltip.{key}", language=language, default=tooltip_default)
    return LaunchBadgeView(phase=normalized, text=text, tooltip=tooltip.format(mode=mode))


def _badge_colors(*, phase: str, theme_name: str) -> tuple[str, str, str]:
    """Цвета значка: текст, фон, фон при наведении."""
    palette = get_semantic_palette(theme_name)
    if phase == "running":
        return palette.success_text, "rgba(108, 203, 95, 0.16)", "rgba(108, 203, 95, 0.28)"
    if phase in BUSY_LAUNCH_PHASES:
        return palette.warning_text, "rgba(245, 166, 35, 0.16)", "rgba(245, 166, 35, 0.28)"
    if phase == "failed":
        return palette.error_text, palette.error_soft_bg, "rgba(255, 82, 82, 0.26)"
    return palette.neutral_badge_fg, palette.neutral_badge_bg, palette.neutral_badge_bg_hover


def _badge_qss(*, phase: str, theme_name: str) -> str:
    fg, _bg, _hover = _badge_colors(phase=phase, theme_name=theme_name)
    return badge_qss(f"#{LAUNCH_TITLE_BADGE_OBJECT_NAME}", fg=fg, left_padding=BADGE_TEXT_LEFT, disabled=True)


class LaunchTitleBadge(TransparentPushButton):
    """Кнопка-метка состояния Zapret в titleBar.

    Это кнопка, а не QLabel: кнопка сама забирает нажатие мыши, поэтому клик
    по метке не начинает перетаскивание окна.
    """

    def __init__(self, parent=None, *, language_provider: Callable[[], str | None]):
        super().__init__(parent)
        self._language_provider = language_provider
        self._phase = ""
        self._launch_method = ""
        self._styled_phase: str | None = None

        self.setObjectName(LAUNCH_TITLE_BADGE_OBJECT_NAME)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Обе анимации бесконечные и считают фазу по времени, а кадры берут
        # у общего такта: их перерисовка сливается с остальными анимациями окна.
        clock = frame_clock()
        self._breath_t = 0.0
        self._breath = clock.subscribe(self._on_breath_frame, interval_ms=BADGE_FRAME_MS, owner=self)
        self._pulse_t = 0.0
        self._pulse = clock.subscribe(self._on_pulse_frame, interval_ms=BADGE_FRAME_MS, owner=self)
        # Цвета значка для текущей фазы и темы: считаются один раз, а не на каждый кадр.
        self._paint_colors_key: tuple | None = None
        self._paint_colors: tuple[QColor, QColor, QColor] | None = None
        # Готовые картинки кольца с точкой: по одной на шаг пульса.
        self._layers = LayerCache(capacity=PULSE_STEPS + 8)
        self.hide()

    def phase(self) -> str:
        return self._phase

    def set_state(self, *, phase: str, launch_method: str) -> bool:
        """Показывает новую фазу. True — изменились текст или видимость (ширина в titleBar)."""
        phase = normalize_launch_phase(phase)
        launch_method = str(launch_method or "")
        if phase == self._phase and launch_method == self._launch_method and not self.isHidden():
            return False
        self._phase = phase
        self._launch_method = launch_method
        return self._render()

    def retranslate(self) -> bool:
        if not self._phase:
            return False
        return self._render()

    def _render(self) -> bool:
        view = build_launch_badge_view(
            phase=self._phase,
            launch_method=self._launch_method,
            language=self._language_provider(),
        )
        was_hidden = self.isHidden()
        old_text = self.text()

        self._apply_style(view.phase)
        # Во время остановки нажимать нечего — ждём, пока процесс завершится.
        self.setEnabled(view.phase != "stopping")
        if old_text != view.text:
            self.setText(view.text)
            self.adjustSize()
        set_tooltip(self, view.tooltip)
        set_control_accessibility(self, name=f"Состояние Zapret: {view.text}", description=view.tooltip)
        self._sync_breath()
        if was_hidden:
            self.show()
        self.update()
        return was_hidden or old_text != view.text

    # ---- «дыхание» точки во время запуска/остановки --------------------

    def _can_animate(self) -> bool:
        if not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _can_breathe(self) -> bool:
        return self._phase in BUSY_LAUNCH_PHASES and self._can_animate()

    def _can_pulse(self) -> bool:
        return self._phase == "running" and self._can_animate()

    def _sync_breath(self) -> None:
        if self._can_breathe():
            if not self._breath.isActive():
                self._breath.start()
        else:
            self._breath.stop()
            self._breath_t = 0.0
        if self._can_pulse():
            if not self._pulse.isActive():
                self._pulse.start()
        else:
            self._pulse.stop()
            self._pulse_t = 0.0

    def is_pulsing(self) -> bool:
        return self._pulse.isActive()

    def _on_pulse_frame(self) -> None:
        phase = (self._pulse.elapsed_ms() % RUNNING_PULSE_MS) / RUNNING_PULSE_MS
        self._pulse_t = int(phase * PULSE_STEPS) / PULSE_STEPS
        self.update()

    def is_breathing(self) -> bool:
        return self._breath.isActive()

    def _on_breath_frame(self) -> None:
        self._breath_t = (self._breath.elapsed_ms() % BUSY_BREATH_MS) / BUSY_BREATH_MS
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_breath()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._breath.stop()
        self._pulse.stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_breath()

    def _colors_for_paint(self) -> tuple[QColor, QColor, QColor]:
        """Фон, фон при наведении и цвет точки для текущей фазы и темы."""
        theme_name = current_theme_name()
        # Палитра темы — один и тот же объект, пока тема и акцент не менялись.
        key = (self._phase, theme_name, id(get_theme_tokens(theme_name)))
        if key != self._paint_colors_key or self._paint_colors is None:
            _fg, background, hover = _badge_colors(phase=self._phase, theme_name=theme_name)
            self._paint_colors = (
                to_qcolor(background),
                to_qcolor(hover),
                QColor(phase_color(self._phase) or STOPPED_DOT_COLOR),
            )
            self._paint_colors_key = key
        return self._paint_colors

    def paintEvent(self, event) -> None:  # noqa: N802
        background, hover, color = self._colors_for_paint()
        shape = badge_shape(self)
        # Фон со сглаженными углами — до текста кнопки.
        hovered = bool(getattr(self, "isHover", False)) and not bool(getattr(self, "isPressed", False))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(shape, hover if hovered else background)
        painter.end()
        super().paintEvent(event)
        if not self._phase:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        center = QPointF(BADGE_DOT_LEFT, self.height() / 2)

        if self.is_pulsing():
            p = self._pulse_t
            # Подложка в такт мягко светлеет и гаснет.
            glow = QColor(color)
            glow.setAlphaF(0.2 * math.sin(math.pi * p))
            painter.fillPath(shape, glow)
            # Кольцо, ореол и точка этого шага пульса — готовой картинкой.
            self._layers.draw(
                painter,
                ("pulse", round(p * PULSE_STEPS), color.rgba(), self.width(), self.height()),
                QRectF(0.0, 0.0, PULSE_LAYER_WIDTH, float(self.height())),
                lambda layer: self._paint_dot(layer, shape, center, color, ring_t=p, halo=1.0),
            )
        elif self.is_breathing():
            # Запуск или остановка: ореол дышит.
            strength = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2.0 * math.pi * self._breath_t))
            self._paint_dot(painter, shape, center, color, ring_t=None, halo=strength)
        else:
            self._paint_dot(painter, shape, center, color, ring_t=None, halo=1.0 if self._phase == "running" else None)
        painter.end()

    @staticmethod
    def _paint_dot(painter, shape, center: QPointF, color: QColor, *, ring_t: float | None, halo: float | None) -> None:
        """Точка состояния: расходящееся кольцо (``ring_t`` — фаза 0..1), мягкий ореол и сама точка."""
        if ring_t is not None:
            painter.save()
            painter.setClipPath(shape)
            # Кольцо расходится от точки и тает.
            ring = QColor(color)
            ring.setAlphaF(0.95 * (1.0 - ring_t) ** 1.3)
            painter.setPen(QPen(ring, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = BADGE_DOT_RADIUS + 1.0 + RUNNING_RING_GROWTH * (1.0 - (1.0 - ring_t) ** 2)
            painter.drawEllipse(center, radius, radius)
            painter.restore()
        painter.setPen(Qt.PenStyle.NoPen)

        if halo is not None:
            # Мягкий ореол: у «работает» постоянный, у запуска/остановки дышит.
            soft = QColor(color)
            soft.setAlphaF(0.35 * halo)
            painter.setBrush(soft)
            radius = BADGE_DOT_RADIUS + 2.5 * halo
            painter.drawEllipse(center, radius, radius)

        painter.setBrush(color)
        painter.drawEllipse(center, BADGE_DOT_RADIUS, BADGE_DOT_RADIUS)

    def _apply_style(self, phase: str) -> None:
        style_key = "busy" if phase in BUSY_LAUNCH_PHASES else phase
        if self._styled_phase == style_key:
            return
        self._styled_phase = style_key
        setCustomStyleSheet(
            self,
            _badge_qss(phase=phase, theme_name="light"),
            _badge_qss(phase=phase, theme_name="dark"),
        )


__all__ = [
    "LAUNCH_TITLE_BADGE_OBJECT_NAME",
    "LaunchBadgeView",
    "LaunchTitleBadge",
    "build_launch_badge_view",
]
