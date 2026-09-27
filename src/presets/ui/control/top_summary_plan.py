from __future__ import annotations

from app.ui_texts import tr as tr_catalog
from donater.premium_display import PremiumDisplay, format_days_left


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


def build_premium_summary(display: PremiumDisplay, *, language: str) -> tuple[str, str]:
    if not display.is_known:
        # Первая проверка ещё идёт: это не Free, а «пока не знаем».
        return (
            tr_catalog("page.control.summary.premium.checking", language=language, default="Проверка..."),
            tr_catalog(
                "page.control.summary.premium.checking_details",
                language=language,
                default="Узнаём статус подписки",
            ),
        )
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
