"""Обучающий тур по программе: публичные двери для окна и post-startup."""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget


def find_onboarding_overlay(window):
    if window is None or sip.isdeleted(window):
        return None
    from ui.onboarding.overlay import OVERLAY_OBJECT_NAME, OnboardingOverlay

    # Оверлей — прямой потомок окна: не обходим всё дерево страниц.
    for overlay in window.findChildren(
        OnboardingOverlay,
        OVERLAY_OBJECT_NAME,
        Qt.FindChildOption.FindDirectChildrenOnly,
    ):
        if not sip.isdeleted(overlay) and not overlay.is_finishing():
            return overlay
    return None


def is_onboarding_tour_active(window) -> bool:
    return find_onboarding_overlay(window) is not None


def _window_ready_for_automatic_tour(window: QWidget) -> bool:
    if not window.isVisible() or window.isMinimized():
        return False
    # Не накрываем туром диалог обновления или любое другое модальное окно.
    if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
        return False
    return True


def start_onboarding_tour(window, *, automatic: bool = False) -> bool:
    """Показывает тур поверх окна.

    automatic=True — первый запуск: тур стартует, только если окно видно
    и не занято диалогом. Возвращает True, если тур сейчас на экране
    (в том числе если он уже шёл).
    """
    if window is None or sip.isdeleted(window):
        return False
    if is_onboarding_tour_active(window):
        return True
    if automatic and not _window_ready_for_automatic_tour(window):
        return False

    from ui.navigation.text_sync import resolve_ui_language
    from ui.onboarding.overlay import OnboardingOverlay
    from ui.onboarding.steps import TOUR_STEPS, build_tour_context
    from ui.window_adapter import get_current_page

    context = build_tour_context(window)
    if context.control_page_name is None and automatic:
        # Боковое меню ещё не построено — попробуем чуть позже.
        return False
    context.current_page = get_current_page(window)

    overlay = OnboardingOverlay(
        window,
        context,
        TOUR_STEPS,
        language=resolve_ui_language(window),
    )
    return overlay.start()


def sync_onboarding_overlay_geometry(window) -> None:
    overlay = find_onboarding_overlay(window)
    if overlay is not None:
        overlay.sync_geometry()


__all__ = [
    "find_onboarding_overlay",
    "is_onboarding_tour_active",
    "start_onboarding_tour",
    "sync_onboarding_overlay_geometry",
]
