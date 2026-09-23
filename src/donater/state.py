from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class PremiumState:
    is_premium: bool = False
    days_remaining: int | None = None
    subscription_level: str = "-"
    status_msg: str = "Не активировано"
    source: str = "api"


def premium_state_from_activation_info(activation_info: Mapping[str, Any] | None) -> PremiumState:
    """Единственный перевод словаря PremiumService.check_device_activation в PremiumState."""
    info = activation_info if isinstance(activation_info, Mapping) else {}
    is_premium = bool(info.get("activated") or info.get("is_premium"))
    days_remaining = normalize_days_remaining(info.get("days_remaining")) if is_premium else None
    status_msg = str(info.get("status") or "").strip()
    if not status_msg:
        status_msg = "Premium активен" if is_premium else "Не активировано"
    source = str(info.get("source") or "").strip() or "api"
    subscription_level = str(info.get("subscription_level") or ("zapretik" if is_premium else "-")).strip() or "-"

    return PremiumState(
        is_premium=is_premium,
        days_remaining=days_remaining,
        subscription_level=subscription_level if is_premium else "-",
        status_msg=status_msg,
        source=source,
    )


def normalize_days_remaining(value: Any) -> int | None:
    """Приводит срок подписки к виду, который понимает весь UI.

    None и мусор означают «срок неизвестен» и остаются None — их нельзя
    превращать в 0, иначе UI покажет «истекает сегодня». Отрицательный срок
    сжимается до 0: подписка с таким сроком всё равно заканчивается.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        days = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, days)
