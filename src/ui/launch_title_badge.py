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

from PyQt6.QtCore import QEvent, QPointF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QSizePolicy
from qfluentwidgets import TransparentPushButton, setCustomStyleSheet

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip
from ui.launch_control import BUSY_LAUNCH_PHASES, mode_label_for_launch_method, normalize_launch_phase, phase_color
from ui.theme_semantic import get_semantic_palette


LAUNCH_TITLE_BADGE_OBJECT_NAME = "launchTitleBadge"
BADGE_DOT_RADIUS = 3.5
BADGE_DOT_LEFT = 9
BADGE_TEXT_LEFT = 20
# Пока идёт запуск или остановка, точка мягко «дышит».
BUSY_BREATH_MS = 1200
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


def _badge_qss(*, phase: str, theme_name: str) -> str:
    palette = get_semantic_palette(theme_name)
    if phase == "running":
        fg, bg, bg_hover = palette.success_text, "rgba(108, 203, 95, 0.16)", "rgba(108, 203, 95, 0.28)"
    elif phase in BUSY_LAUNCH_PHASES:
        fg, bg, bg_hover = palette.warning_text, "rgba(245, 166, 35, 0.16)", "rgba(245, 166, 35, 0.28)"
    elif phase == "failed":
        fg, bg, bg_hover = palette.error_text, palette.error_soft_bg, "rgba(255, 82, 82, 0.26)"
    else:
        fg, bg, bg_hover = palette.neutral_badge_fg, palette.neutral_badge_bg, palette.neutral_badge_bg_hover
    selector = f"#{LAUNCH_TITLE_BADGE_OBJECT_NAME}"
    return (
        f"{selector} {{ color: {fg}; background: {bg}; border: none; border-radius: 4px; "
        f"padding: 0px 8px 0px {BADGE_TEXT_LEFT}px; font-size: 10px; font-weight: 600; }}"
        f"{selector}:hover {{ background: {bg_hover}; }}"
        f"{selector}:pressed {{ background: {bg}; }}"
        f"{selector}:disabled {{ color: {fg}; background: {bg}; }}"
    )


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

        self._breath_t = 0.0
        # QVariantAnimation, а не QPropertyAnimation: при выключенных
        # анимациях общий fallback подменяет QPropertyAnimation.start.
        self._breath = QVariantAnimation(self)
        self._breath.setStartValue(0.0)
        self._breath.setEndValue(1.0)
        self._breath.setDuration(BUSY_BREATH_MS)
        self._breath.setLoopCount(-1)
        self._breath.valueChanged.connect(self._on_breath_value)
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

    def _can_breathe(self) -> bool:
        if self._phase not in BUSY_LAUNCH_PHASES or not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _sync_breath(self) -> None:
        if self._can_breathe():
            if self._breath.state() != QVariantAnimation.State.Running:
                self._breath.start()
        else:
            self._breath.stop()
            self._breath_t = 0.0

    def is_breathing(self) -> bool:
        return self._breath.state() == QVariantAnimation.State.Running

    def _on_breath_value(self, value) -> None:
        try:
            self._breath_t = float(value)
        except (TypeError, ValueError):
            return
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_breath()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._breath.stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_breath()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._phase:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = QColor(phase_color(self._phase) or STOPPED_DOT_COLOR)
        center = QPointF(BADGE_DOT_LEFT, self.height() / 2)

        if self._phase == "running" or self.is_breathing():
            # Мягкий ореол: у «работает» постоянный, у запуска/остановки дышит.
            strength = 1.0
            if self.is_breathing():
                strength = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2.0 * math.pi * self._breath_t))
            halo = QColor(color)
            halo.setAlphaF(0.35 * strength)
            painter.setBrush(halo)
            radius = BADGE_DOT_RADIUS + 2.5 * strength
            painter.drawEllipse(center, radius, radius)

        painter.setBrush(color)
        painter.drawEllipse(center, BADGE_DOT_RADIUS, BADGE_DOT_RADIUS)
        painter.end()

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
