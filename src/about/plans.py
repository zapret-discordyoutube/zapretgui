from __future__ import annotations

from dataclasses import dataclass

from app.ui_texts import tr as tr_catalog
from donater.premium_display import build_premium_display, days_unit


@dataclass(slots=True)
class AboutTabSwitchPlan:
    current_index: int
    route_key: str
    init_help: bool
    init_kvn: bool


@dataclass(slots=True)
class AboutSubscriptionPlan:
    icon_name: str
    icon_color: str
    label_text: str

TAB_KEYS = ("about", "help", "kvn")

def build_tab_switch_plan(
    *,
    index: int,
    help_initialized: bool,
    kvn_initialized: bool,
) -> AboutTabSwitchPlan:
    safe_index = max(0, min(int(index), len(TAB_KEYS) - 1))
    route_key = TAB_KEYS[safe_index]
    return AboutTabSwitchPlan(
        current_index=safe_index,
        route_key=route_key,
        init_help=(safe_index == 1 and not help_initialized),
        init_kvn=(safe_index == 2 and not kvn_initialized),
    )

def resolve_tab_index(key: str) -> int | None:
    normalized = str(key or "").strip().lower()
    if normalized == "support":
        return 0
    if normalized in TAB_KEYS:
        return TAB_KEYS.index(normalized)
    return None

def build_subscription_status_plan(
    *,
    is_premium: bool,
    days: int | None,
    language: str,
    free_icon_color: str,
    premium_icon_color: str,
) -> AboutSubscriptionPlan:
    display = build_premium_display(is_premium=is_premium, days_remaining=days)
    if not display.is_premium:
        return AboutSubscriptionPlan(
            icon_name="fa5s.user",
            icon_color=free_icon_color,
            label_text=tr_catalog("page.about.subscription.free", language=language, default="Free версия"),
        )

    if display.days is None:
        label_text = tr_catalog(
            "page.about.subscription.premium_active",
            language=language,
            default="Premium активен",
        )
    else:
        label_text = tr_catalog(
            "page.about.subscription.premium_days",
            language=language,
            default="Premium (осталось {days} {unit})",
        ).format(days=display.days, unit=days_unit(display.days, language=language))
    return AboutSubscriptionPlan(
        icon_name="fa5s.star",
        icon_color=premium_icon_color,
        label_text=label_text,
    )
