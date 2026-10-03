"""Метка FREE / PREMIUM в верхней панели окна.

Метка только показывает готовый PremiumDisplay и по клику открывает страницу
Premium. Сама она статус не вычисляет: уровень и срок приходят из общего
UI-store через ui/window_state_binder.py.
"""

from __future__ import annotations

from collections.abc import Callable

import math

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy
from qfluentwidgets import TransparentPushButton, setCustomStyleSheet

from app.ui_texts import tr as tr_catalog
from donater.premium_display import TIER_UNKNOWN, PremiumDisplay, format_days_left
from ui.accessibility import set_control_accessibility
from ui.animation_policy import are_live_animations_enabled
from ui.fluent_widgets import set_tooltip
from ui.frame_clock import BASE_FRAME_MS, frame_clock
from ui.theme_semantic import get_semantic_palette
from ui.title_badge_paint import badge_qss, badge_shape, current_theme_name, paint_badge_body
from ui.widgets.star_glyph import paint_star


SUBSCRIPTION_TITLE_BADGE_OBJECT_NAME = "subscriptionTitleBadge"
# Звезда у PREMIUM рисуется кодом (не эмодзи), см. ui/widgets/star_glyph.py.
PREMIUM_STAR_SIZE = 12
PREMIUM_STAR_LEFT = 7
# Раз в несколько секунд метка PREMIUM мягко разгорается золотом и гаснет:
# звезда вспыхивает со свечением и искрой, по метке пробегает блик.
# Между вспышками кадры не рисуются — ждёт только одиночный таймер.
PREMIUM_SHINE_INTERVAL_MS = 6000
PREMIUM_SHINE_DURATION_MS = 1600
PREMIUM_SHINE_FIRST_DELAY_MS = 1200
PREMIUM_GLOW_GOLD = "#fbbf24"


def build_title_badge_texts(display: PremiumDisplay, *, language: str | None) -> tuple[str, str]:
    """Возвращает (текст метки, подсказку). Пустой текст — метку не показывать."""
    if not display.is_known:
        return "", ""

    if not display.is_premium:
        return (
            tr_catalog("titlebar.subscription.free", language=language, default="FREE"),
            tr_catalog(
                "titlebar.subscription.free.tooltip",
                language=language,
                default="Бесплатная версия. Нажмите, чтобы узнать о Premium",
            ),
        )

    if display.days is None:
        return (
            tr_catalog("titlebar.subscription.premium", language=language, default="PREMIUM"),
            tr_catalog(
                "titlebar.subscription.premium.tooltip",
                language=language,
                default="Premium активен. Нажмите, чтобы открыть страницу подписки",
            ),
        )

    return (
        tr_catalog(
            "titlebar.subscription.premium_days",
            language=language,
            default="PREMIUM · {days} дн.",
        ).format(days=display.days),
        tr_catalog(
            "titlebar.subscription.premium_days.tooltip",
            language=language,
            default="Premium активен. {days_left}. Нажмите, чтобы открыть страницу подписки",
        ).format(days_left=format_days_left(display.days, language=language)),
    )


def _badge_colors(*, is_premium: bool, theme_name: str) -> tuple[str, str, str]:
    """Цвета значка: текст, фон, фон при наведении."""
    palette = get_semantic_palette(theme_name)
    if is_premium:
        return palette.premium_fg, palette.premium_bg, palette.premium_bg_hover
    return palette.neutral_badge_fg, palette.neutral_badge_bg, palette.neutral_badge_bg_hover


def _badge_qss(*, is_premium: bool, theme_name: str) -> str:
    fg, _bg, _hover = _badge_colors(is_premium=is_premium, theme_name=theme_name)
    left = PREMIUM_STAR_LEFT + PREMIUM_STAR_SIZE + 4 if is_premium else 8
    return badge_qss(f"#{SUBSCRIPTION_TITLE_BADGE_OBJECT_NAME}", fg=fg, left_padding=left)


class SubscriptionTitleBadge(TransparentPushButton):
    """Кнопка-метка статуса подписки в titleBar.

    Это кнопка, а не QLabel: кнопка сама забирает нажатие мыши, поэтому клик
    по метке не начинает перетаскивание окна.
    """

    def __init__(self, parent=None, *, language_provider: Callable[[], str | None]):
        super().__init__(parent)
        self._language_provider = language_provider
        self._display = PremiumDisplay(tier=TIER_UNKNOWN)
        self._styled_as_premium: bool | None = None

        self.setObjectName(SUBSCRIPTION_TITLE_BADGE_OBJECT_NAME)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._shine_t = 0.0
        # Блик быстрый (полоса пробегает значок за 1,6 с), поэтому частота
        # прежняя, 60 кадров. Но кадры берёт у общего такта: они совпадают с
        # кадрами соседнего значка «Работает» и не дают отдельных перерисовок окна.
        self._shine = frame_clock().subscribe(self._on_shine_frame, interval_ms=BASE_FRAME_MS, owner=self)
        self._shine_timer = QTimer(self)
        self._shine_timer.setSingleShot(True)
        self._shine_timer.timeout.connect(self.play_shine)
        self.hide()

    def display(self) -> PremiumDisplay:
        return self._display

    def set_display(self, display: PremiumDisplay) -> bool:
        """Показывает новый статус. True — изменились текст или видимость (ширина в titleBar)."""
        if display == self._display:
            return False
        self._display = display
        return self._render()

    def retranslate(self) -> bool:
        return self._render()

    def _render(self) -> bool:
        text, tooltip = build_title_badge_texts(self._display, language=self._language_provider())
        was_hidden = self.isHidden()
        old_text = self.text()

        if not text:
            if not was_hidden:
                self.hide()
            return not was_hidden

        self._apply_style(is_premium=self._display.is_premium)
        if self._display.is_premium:
            if not self._shine_timer.isActive() and not self.is_shining():
                self._schedule_shine(PREMIUM_SHINE_FIRST_DELAY_MS)
        else:
            self._stop_shine()
        if old_text != text:
            self.setText(text)
            self.adjustSize()
        set_tooltip(self, tooltip)
        set_control_accessibility(self, name=f"Статус подписки: {text}", description=tooltip)
        if was_hidden:
            self.show()
        return was_hidden or old_text != text

    # ---- вспышка PREMIUM ----------------------------------------------

    def _can_shine(self) -> bool:
        if not self._display.is_premium or not self.isVisible():
            return False
        window = self.window()
        if window is not None and window.isMinimized():
            return False
        return are_live_animations_enabled()

    def _schedule_shine(self, delay_ms: int = PREMIUM_SHINE_INTERVAL_MS) -> None:
        self._shine_timer.stop()
        if self._can_shine():
            self._shine_timer.start(delay_ms)

    def play_shine(self) -> None:
        if not self._can_shine():
            return
        self._shine.stop()
        self._shine_t = 0.0
        self._shine.start()

    def is_shining(self) -> bool:
        return self._shine.isActive()

    def _on_shine_frame(self) -> None:
        progress = self._shine.elapsed_ms() / PREMIUM_SHINE_DURATION_MS
        if progress >= 1.0:
            self._shine.stop()
            self._on_shine_finished()
            return
        self._shine_t = progress
        self.update()

    def _on_shine_finished(self) -> None:
        self._shine_t = 0.0
        self.update()
        self._schedule_shine()

    def _stop_shine(self) -> None:
        self._shine_timer.stop()
        self._shine.stop()
        self._shine_t = 0.0

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_shine(PREMIUM_SHINE_FIRST_DELAY_MS)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._stop_shine()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            window = self.window()
            if window is not None and window.isMinimized():
                self._stop_shine()
            else:
                self._schedule_shine(PREMIUM_SHINE_FIRST_DELAY_MS)

    def paintEvent(self, event) -> None:  # noqa: N802
        _fg, background, hover = _badge_colors(
            is_premium=self._display.is_premium,
            theme_name=current_theme_name(),
        )
        # Фон со сглаженными углами — до текста кнопки.
        paint_badge_body(self, background=background, hover_background=hover)
        super().paintEvent(event)
        if not self._display.is_premium:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._shine_t if self.is_shining() else 0.0
        # Разгорание и затухание: быстро вверх, плавно вниз.
        glow = math.sin(math.pi * min(1.0, t / 0.35)) if t < 0.35 else (1.0 - (t - 0.35) / 0.65) ** 1.5
        glow = max(0.0, glow) if t > 0.0 else 0.0
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = badge_shape(self)
        gold = QColor(PREMIUM_GLOW_GOLD)

        if glow > 0.0:
            # Метка целиком мягко наливается золотом и тонко очерчивается.
            fill = QColor(gold)
            fill.setAlphaF(0.22 * glow)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPath(shape)
            edge = QColor(gold)
            edge.setAlphaF(0.75 * glow)
            painter.setPen(edge)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(shape)

        if 0.12 < t < 0.8:
            # Косой блик пробегает по метке слева направо.
            p = (t - 0.12) / 0.68
            band = body.width() * 0.35
            x = body.left() - band + (body.width() + band * 2) * p
            gradient = QLinearGradient(QPointF(x - band / 2, 0.0), QPointF(x + band / 2, body.height()))
            gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
            gradient.setColorAt(0.5, QColor(255, 244, 214, round(120 * math.sin(math.pi * p))))
            gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawPath(shape)

        center = QPointF(PREMIUM_STAR_LEFT + PREMIUM_STAR_SIZE / 2, self.height() / 2)
        if glow > 0.0:
            halo = QRadialGradient(center, PREMIUM_STAR_SIZE * 1.1)
            inner = QColor(gold)
            inner.setAlphaF(0.85 * glow)
            outer = QColor(gold)
            outer.setAlphaF(0.0)
            halo.setColorAt(0.0, inner)
            halo.setColorAt(1.0, outer)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, PREMIUM_STAR_SIZE * 1.1, PREMIUM_STAR_SIZE * 1.1)

        painter.save()
        painter.translate(center)
        painter.rotate(18.0 * math.sin(2.0 * math.pi * t) * (1.0 - t))
        scale = 1.0 + 0.3 * glow
        painter.scale(scale, scale)
        paint_star(painter, QPointF(0.0, 0.0), PREMIUM_STAR_SIZE / 2)
        painter.restore()

        if glow > 0.3:
            # Искорка у верхнего кончика звезды.
            spark = QColor(255, 255, 255)
            spark.setAlphaF(min(1.0, glow))
            r = 4.0 * glow
            cx, cy = center.x() + 5.0, center.y() - 5.0
            path = QPainterPath()
            path.moveTo(cx, cy - r)
            path.quadTo(cx, cy, cx + r, cy)
            path.quadTo(cx, cy, cx, cy + r)
            path.quadTo(cx, cy, cx - r, cy)
            path.quadTo(cx, cy, cx, cy - r)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(spark)
            painter.drawPath(path)
        painter.end()

    def _apply_style(self, *, is_premium: bool) -> None:
        if self._styled_as_premium is is_premium:
            return
        self._styled_as_premium = is_premium
        setCustomStyleSheet(
            self,
            _badge_qss(is_premium=is_premium, theme_name="light"),
            _badge_qss(is_premium=is_premium, theme_name="dark"),
        )


__all__ = [
    "SUBSCRIPTION_TITLE_BADGE_OBJECT_NAME",
    "SubscriptionTitleBadge",
    "build_title_badge_texts",
]
