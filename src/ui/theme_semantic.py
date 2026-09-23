"""Semantic color helpers for status-oriented UI elements."""

from __future__ import annotations

from dataclasses import dataclass

from ui.theme import get_theme_tokens


@dataclass(frozen=True)
class SemanticPalette:
    on_color: str

    success: str
    warning: str
    error: str
    info: str

    warning_soft: str
    warning_soft_bg: str
    warning_button: str
    warning_button_hover: str
    warning_button_disabled: str

    error_soft_bg: str
    error_soft_border: str

    danger_bg_soft: str
    danger_bg: str
    danger_bg_strong: str

    danger_button: str
    danger_button_hover: str
    success_button: str
    success_button_hover: str

    success_badge: str
    success_soft_bg: str
    success_soft_border: str

    # Янтарные Premium-метки и серая метка Free.
    premium_fg: str
    premium_bg: str
    premium_bg_hover: str
    neutral_badge_fg: str
    neutral_badge_bg: str
    neutral_badge_bg_hover: str


def get_semantic_palette(theme_name: str | None = None) -> SemanticPalette:
    tokens = get_theme_tokens(theme_name)
    on_color = "rgba(18, 18, 18, 0.92)" if tokens.is_light else "rgba(245, 245, 245, 0.95)"

    return SemanticPalette(
        on_color=on_color,
        success="#6ccb5f",
        warning="#ff9800",
        error="#ff5252",
        info=tokens.accent_hex,
        warning_soft="rgba(255, 152, 0, 0.85)",
        warning_soft_bg="rgba(255, 152, 0, 0.15)",
        warning_button="rgba(255, 152, 0, 0.8)",
        warning_button_hover="rgba(255, 152, 0, 1)",
        warning_button_disabled="rgba(255, 152, 0, 0.4)",
        error_soft_bg="rgba(255, 82, 82, 0.15)",
        error_soft_border="rgba(255, 82, 82, 0.4)",
        danger_bg_soft="rgba(220, 53, 69, 0.70)",
        danger_bg="rgba(220, 53, 69, 0.90)",
        danger_bg_strong="rgba(220, 53, 69, 0.98)",
        danger_button="#dc3545",
        danger_button_hover="#c82333",
        success_button="#28a745",
        success_button_hover="#218838",
        success_badge="#2e7d32",
        success_soft_bg="rgba(108, 203, 95, 0.15)",
        success_soft_border="rgba(108, 203, 95, 0.5)",
        premium_fg="#b45309" if tokens.is_light else "#fbbf24",
        premium_bg="rgba(255, 193, 7, 0.16)",
        premium_bg_hover="rgba(255, 193, 7, 0.28)",
        neutral_badge_fg=tokens.fg_muted,
        neutral_badge_bg="rgba(128, 128, 128, 0.16)",
        neutral_badge_bg_hover="rgba(128, 128, 128, 0.28)",
    )
