"""Метка FREE / PREMIUM в верхней панели окна.

Метка только показывает готовый PremiumDisplay и по клику открывает страницу
Premium. Сама она статус не вычисляет: уровень и срок приходят из общего
UI-store через ui/window_state_binder.py.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSizePolicy
from qfluentwidgets import TransparentPushButton, setCustomStyleSheet

from app.ui_texts import tr as tr_catalog
from donater.premium_display import TIER_UNKNOWN, PremiumDisplay, format_days_left
from ui.accessibility import set_control_accessibility
from ui.fluent_widgets import set_tooltip
from ui.theme_semantic import get_semantic_palette


SUBSCRIPTION_TITLE_BADGE_OBJECT_NAME = "subscriptionTitleBadge"


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


def _badge_qss(*, is_premium: bool, theme_name: str) -> str:
    palette = get_semantic_palette(theme_name)
    if is_premium:
        fg, bg, bg_hover = palette.premium_fg, palette.premium_bg, palette.premium_bg_hover
    else:
        fg, bg, bg_hover = palette.neutral_badge_fg, palette.neutral_badge_bg, palette.neutral_badge_bg_hover
    selector = f"#{SUBSCRIPTION_TITLE_BADGE_OBJECT_NAME}"
    return (
        f"{selector} {{ color: {fg}; background: {bg}; border: none; border-radius: 4px; "
        "padding: 0px 8px; font-size: 10px; font-weight: 600; }"
        f"{selector}:hover {{ background: {bg_hover}; }}"
        f"{selector}:pressed {{ background: {bg}; }}"
    )


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
        if old_text != text:
            self.setText(text)
            self.adjustSize()
        set_tooltip(self, tooltip)
        set_control_accessibility(self, name=f"Статус подписки: {text}", description=tooltip)
        if was_hidden:
            self.show()
        return was_hidden or old_text != text

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
