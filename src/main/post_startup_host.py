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

    def install_idle_memory_trim(self) -> None:
        """Чистит память, пока окно долго скрыто в трее или свёрнуто."""
        from ui.idle_memory_trim import install_idle_memory_trim

        install_idle_memory_trim(self._window)

    def is_alive(self) -> bool:
        close_state = self.close_state
        return not bool(close_state.is_exiting or close_state.closing_completely)

    def is_window_shown(self) -> bool:
        """Окно сейчас на экране: не свёрнуто и не убрано в трей."""
        window = self._window
        if window is None:
            return False
        return bool(window.isVisible()) and not bool(window.isMinimized())

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
        """Собирает страницу про запас вместе с блоками её первого экрана.

        Страница, которая собирается блоками, при показе достраивает первый
        экран. Здесь это делается заранее, чтобы щелчок по подготовленной
        странице не платил и за него.
        """
        from ui.window_adapter import ensure_page

        page = ensure_page(self._window, page_name)
        build_first_screen = getattr(page, "build_first_screen_blocks", None)
        if callable(build_first_screen):
            build_first_screen()
        return page

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
