from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ApplicationLifecycleWindowPort:
    _window: Any

    def persist_geometry(self, *, context: str, level: str = "DEBUG") -> None:
        from main.window_lifecycle_cleanup import persist_window_geometry

        persist_window_geometry(self._window, context=context, level=level)

    def persist_sidebar_state(self, *, context: str, level: str = "DEBUG") -> None:
        from main.window_lifecycle_cleanup import persist_sidebar_state

        persist_sidebar_state(self._window, context=context, level=level)

    def cleanup_theme(self) -> None:
        from main.window_lifecycle_cleanup import cleanup_theme_for_close

        cleanup_theme_for_close(self._window)

    def cleanup_threaded_pages(self) -> None:
        from main.window_lifecycle_cleanup import (
            cleanup_session_runtimes_for_close,
            cleanup_threaded_pages_for_close,
        )

        cleanup_threaded_pages_for_close(self._window)
        cleanup_session_runtimes_for_close(self._window)

    def cleanup_visual_and_proxy_resources(self, *, telegram_proxy_feature) -> None:
        from main.window_lifecycle_cleanup import cleanup_visual_and_proxy_resources_for_close

        cleanup_visual_and_proxy_resources_for_close(
            self._window,
            telegram_proxy_feature=telegram_proxy_feature,
        )


    def finish_strategy_scan_before_exit(self, on_done, *, timeout_ms: int = 60_000) -> bool:
        """Если идёт подбор стратегии, остановить его и вернуть Zapret до выхода.

        ``on_done(restored)`` вызывается один раз: когда подбор закончился или
        когда вышло время (тогда ``restored`` False). Возвращает False, если
        подбора нет и ждать нечего.
        """
        from PyQt6.QtCore import QTimer

        from main.window_lifecycle_cleanup import iter_loaded_pages_for_close

        state = {"done": False}
        # Таймер принадлежит окну: пока идёт ожидание, ссылок на него больше нет.
        timer = QTimer(self._window)
        timer.setSingleShot(True)

        def _once(restored: bool) -> None:
            if state["done"]:
                return
            state["done"] = True
            timer.stop()
            on_done(bool(restored))

        timer.timeout.connect(lambda: _once(False))
        for _name, page in iter_loaded_pages_for_close(self._window):
            request = getattr(page, "request_exit_restore", None)
            if not callable(request):
                continue
            if request(_once):
                timer.start(int(timeout_ms))
                return True
        return False

    def begin_exit_screen(self, *, stop_dpi: bool, bypass_running: bool):
        """Прощальный экран поверх окна. Возвращает сеанс с finish(on_done)."""
        from ui.exit_screen import begin_exit_screen
        from ui.navigation.text_sync import resolve_ui_language

        return begin_exit_screen(
            self._window,
            stop_dpi=stop_dpi,
            bypass_running=bypass_running,
            language=resolve_ui_language(self._window),
        )


def build_application_lifecycle_window_port(window) -> ApplicationLifecycleWindowPort:
    return ApplicationLifecycleWindowPort(window)


__all__ = ["ApplicationLifecycleWindowPort", "build_application_lifecycle_window_port"]
