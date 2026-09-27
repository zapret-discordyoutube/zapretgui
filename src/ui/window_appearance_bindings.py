from __future__ import annotations

from ui.window_appearance_state import (
    on_animations_changed,
    on_editor_smooth_scroll_changed,
    on_smooth_scroll_changed,
)


def initialize_window_appearance_bindings(window) -> None:
    """Применяет сохранённые настройки внешнего вида к окну при старте."""
    from settings.appearance import (
        peek_warmed_animations_enabled,
        peek_warmed_editor_smooth_scroll_enabled,
        peek_warmed_smooth_scroll_enabled,
    )

    on_animations_changed(window, bool(peek_warmed_animations_enabled()))
    on_smooth_scroll_changed(window, bool(peek_warmed_smooth_scroll_enabled()))
    on_editor_smooth_scroll_changed(window, bool(peek_warmed_editor_smooth_scroll_enabled()))


__all__ = [
    "initialize_window_appearance_bindings",
]
