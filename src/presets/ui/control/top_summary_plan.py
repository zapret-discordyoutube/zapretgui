from __future__ import annotations

from app.ui_texts import tr as tr_catalog
from donater.premium_display import build_premium_display, format_days_left


def build_profiles_value(enabled_count: int | None, *, language: str) -> str:
    if enabled_count is None:
        return tr_catalog(
            "page.control.summary.profiles.unavailable",
            language=language,
            default="Проверяем...",
        )
    return tr_catalog(
        "page.control.summary.profiles.enabled_template",
        language=language,
        default="{count} включено",
    ).format(count=max(0, int(enabled_count)))


def build_premium_summary(
    is_premium: bool,
    days_remaining: int | None,
    *,
    language: str,
) -> tuple[str, str]:
    display = build_premium_display(is_premium=is_premium, days_remaining=days_remaining)
    if not display.is_premium:
        return (
            tr_catalog("common.premium.tier.free", language=language, default="Free"),
            tr_catalog(
                "page.control.summary.premium.free_details",
                language=language,
                default="Базовые функции",
            ),
        )

    title = tr_catalog("common.premium.tier.premium", language=language, default="Premium")
    if display.days is not None:
        return title, format_days_left(display.days, language=language)
    return (
        title,
        tr_catalog(
            "page.control.summary.premium.active_details",
            language=language,
            default="Активен",
        ),
    )
