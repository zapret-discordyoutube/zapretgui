"""Единые правила показа Premium-статуса во всём интерфейсе.

Заголовок окна, страница Premium, «О программе» и сводка на главной берут
отсюда уровень подписки, пороги срока и склонение «день/дня/дней».
Своих порогов и своей обработки «срок неизвестен» у них быть не должно.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ui_texts import normalize_language, tr as tr_catalog
from donater.state import normalize_days_remaining


TIER_UNKNOWN = "unknown"
TIER_FREE = "free"
TIER_ACTIVE = "active"
TIER_WARNING = "warning"
TIER_URGENT = "urgent"

PREMIUM_TIERS = frozenset({TIER_ACTIVE, TIER_WARNING, TIER_URGENT})

# Больше 30 дней — всё спокойно, 8–30 — пора продлевать, 7 и меньше — срочно.
WARNING_DAYS_THRESHOLD = 30
URGENT_DAYS_THRESHOLD = 7

_DAYS_UNIT_DEFAULTS = {
    "ru": {"one": "день", "few": "дня", "many": "дней"},
    "en": {"one": "day", "few": "days", "many": "days"},
}


@dataclass(frozen=True, slots=True)
class PremiumDisplay:
    """Что показать пользователю: уровень подписки и срок (None — неизвестен)."""

    tier: str
    days: int | None = None

    @property
    def is_premium(self) -> bool:
        return self.tier in PREMIUM_TIERS

    @property
    def is_known(self) -> bool:
        return self.tier != TIER_UNKNOWN


def build_premium_display(
    *,
    is_premium: bool,
    days_remaining: Any,
    known: bool = True,
) -> PremiumDisplay:
    if not known:
        return PremiumDisplay(tier=TIER_UNKNOWN)
    if not is_premium:
        return PremiumDisplay(tier=TIER_FREE)

    days = normalize_days_remaining(days_remaining)
    if days is None:
        return PremiumDisplay(tier=TIER_ACTIVE)
    if days > WARNING_DAYS_THRESHOLD:
        return PremiumDisplay(tier=TIER_ACTIVE, days=days)
    if days > URGENT_DAYS_THRESHOLD:
        return PremiumDisplay(tier=TIER_WARNING, days=days)
    return PremiumDisplay(tier=TIER_URGENT, days=days)


def premium_display_from_ui_state(state: Any) -> PremiumDisplay:
    """Читает Premium-поля из AppUiState (или похожего снимка)."""
    return build_premium_display(
        is_premium=bool(getattr(state, "subscription_is_premium", False)),
        days_remaining=getattr(state, "subscription_days_remaining", None),
        known=bool(getattr(state, "subscription_known", False)),
    )


def _plural_form(days: int, language: str) -> str:
    count = abs(int(days))
    if language == "ru":
        if count % 10 == 1 and count % 100 != 11:
            return "one"
        if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
            return "few"
        return "many"
    return "one" if count == 1 else "many"


def days_unit(days: int, *, language: str | None) -> str:
    """«день/дня/дней» или «day/days» для числа дней."""
    lang = normalize_language(language)
    form = _plural_form(days, lang)
    return tr_catalog(
        f"common.premium.days_unit.{form}",
        language=lang,
        default=_DAYS_UNIT_DEFAULTS.get(lang, _DAYS_UNIT_DEFAULTS["ru"])[form],
    )


def format_days_left(days: int, *, language: str | None) -> str:
    """«Осталось 5 дней» / «5 days left»."""
    return tr_catalog(
        "common.premium.days_left",
        language=language,
        default="Осталось {days} {unit}",
    ).format(days=days, unit=days_unit(days, language=language))


__all__ = [
    "PREMIUM_TIERS",
    "PremiumDisplay",
    "TIER_ACTIVE",
    "TIER_FREE",
    "TIER_UNKNOWN",
    "TIER_URGENT",
    "TIER_WARNING",
    "URGENT_DAYS_THRESHOLD",
    "WARNING_DAYS_THRESHOLD",
    "build_premium_display",
    "days_unit",
    "format_days_left",
    "premium_display_from_ui_state",
]
