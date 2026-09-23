"""Подписки главного окна на общий UI-store.

Архитектурная проверка разрешает окну подписываться на store только отсюда
(app/architecture_checks.py: check_no_window_level_state_subscriptions).
"""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QTimer

from app.page_names import PageName
from donater.premium_display import premium_display_from_ui_state
from ui.navigation.text_sync import resolve_ui_language
from ui.subscription_title_badge import SubscriptionTitleBadge
from ui.window_adapter import show_page, sync_titlebar_search_width
from ui.window_ui_session import get_window_ui_session


SUBSCRIPTION_BADGE_FIELDS = frozenset(
    {
        "subscription_known",
        "subscription_is_premium",
        "subscription_days_remaining",
    }
)


def _window_language(window) -> str:
    session = get_window_ui_session(window)
    if session is not None:
        return session.ui_language
    return resolve_ui_language(window)


def _subscription_badge_insert_index(window, layout) -> int:
    title_label = getattr(window.titleBar, "titleLabel", None)
    if title_label is not None:
        title_index = layout.indexOf(title_label)
        if title_index >= 0:
            return title_index + 1

    session = get_window_ui_session(window)
    search_widget = None if session is None else session.sidebar_search_nav_widget
    if search_widget is not None:
        search_index = layout.indexOf(search_widget)
        if search_index >= 0:
            return search_index

    return max(0, layout.count() - 1)


def bind_subscription_title_badge(window, ui_state_store) -> SubscriptionTitleBadge | None:
    """Ставит метку FREE/PREMIUM после названия окна и подписывает её на store."""
    title_bar = getattr(window, "titleBar", None)
    layout = getattr(title_bar, "hBoxLayout", None)
    if title_bar is None or layout is None:
        return None

    existing = title_bar.findChild(SubscriptionTitleBadge)
    if existing is not None:
        return existing

    badge = SubscriptionTitleBadge(title_bar, language_provider=lambda: _window_language(window))
    layout.insertWidget(_subscription_badge_insert_index(window, layout), badge)
    badge.clicked.connect(lambda: show_page(window, PageName.PREMIUM))

    def _sync_search_width() -> None:
        if not sip.isdeleted(window):
            sync_titlebar_search_width(window)

    def _on_ui_state_changed(state, _changed_fields: frozenset[str]) -> None:
        if sip.isdeleted(badge):
            return
        if badge.set_display(premium_display_from_ui_state(state)):
            # Поиск в titleBar центрируется по ширине соседей; новая ширина
            # метки становится известна только после применения layout.
            QTimer.singleShot(0, _sync_search_width)

    unsubscribe = ui_state_store.subscribe(
        _on_ui_state_changed,
        fields=SUBSCRIPTION_BADGE_FIELDS,
        emit_initial=True,
    )

    def _unsubscribe_on_destroy(*_args) -> None:
        # При закрытии приложения store может быть уже разобран раньше метки.
        try:
            unsubscribe()
        except Exception:
            pass

    badge.destroyed.connect(_unsubscribe_on_destroy)
    return badge


def retranslate_subscription_title_badge(window) -> None:
    title_bar = getattr(window, "titleBar", None)
    if title_bar is None:
        return
    badge = title_bar.findChild(SubscriptionTitleBadge)
    if badge is not None and badge.retranslate():
        sync_titlebar_search_width(window)


def bind_window_ui_state(window, ui_state_store) -> None:
    """Все подписки окна на store (сейчас — только метка подписки в titleBar)."""
    bind_subscription_title_badge(window, ui_state_store)


__all__ = [
    "SUBSCRIPTION_BADGE_FIELDS",
    "bind_subscription_title_badge",
    "bind_window_ui_state",
    "retranslate_subscription_title_badge",
]
