from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PostStartupHost:
    _window: Any

    @property
    def close_state(self):
        return self._window.close_state

    @property
    def startup_state(self):
        return self._window.startup_state

    @property
    def startup_interactive_ready(self):
        return self._window.startup_interactive_ready

    @property
    def startup_post_init_ready(self):
        return self._window.startup_post_init_ready

    def is_alive(self) -> bool:
        close_state = self.close_state
        return not bool(close_state.is_exiting or close_state.closing_completely)

    def show_whats_new(self, version: str, history) -> bool:
        """Окно «Что нового» после обновления. False — окно программы не готово."""
        window = self._window
        if window is None or not window.isVisible() or window.isMinimized():
            return False
        from ui.navigation.text_sync import resolve_ui_language
        from updater.ui.update_dialog import show_whats_new_dialog

        show_whats_new_dialog(
            window,
            version=version,
            history=history,
            language=resolve_ui_language(window),
        )
        return True

    def show_page(self, page_name) -> None:
        from ui.window_adapter import show_page

        show_page(self._window, page_name)

    def ensure_page(self, page_name):
        from ui.window_adapter import ensure_page

        return ensure_page(self._window, page_name)

    def start_onboarding_tour(self) -> bool:
        """Пробует показать обучающий тур. False — окно пока не готово."""
        from ui.onboarding import start_onboarding_tour

        return bool(start_onboarding_tour(self._window, automatic=True))

    def get_loaded_page(self, page_name):
        from ui.window_adapter import get_loaded_page

        return get_loaded_page(self._window, page_name)


def build_post_startup_host(window) -> PostStartupHost:
    return PostStartupHost(window)


__all__ = ["PostStartupHost", "build_post_startup_host"]
