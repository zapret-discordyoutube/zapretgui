from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .strategy_shape import payload_badge_text


@dataclass(frozen=True)
class ProfileDisplayItem:
    key: str
    persistent_key: str
    profile_index: int
    display_name: str
    enabled: bool
    in_preset: bool
    strategy_id: str
    strategy_name: str
    match_lines: tuple[str, ...]
    list_type: str
    rating: str
    favorite: bool
    group: str
    group_name: str
    order: int
    # Исходный порядок из файла пресета/шаблонов — вход для резолвера порядка,
    # НЕ для отображения. Отображаемая позиция — всегда `order`.
    source_order: int = 0
    group_rank: int = 10_000
    group_collapsed: bool = False
    user_profile_id: str = ""
    # Типы пакетов веток составной стратегии («TLS · HTTP»), пусто — обычная.
    strategy_payload_badge: str = ""


def build_profile_display_items(items: tuple[Any, ...]) -> tuple[ProfileDisplayItem, ...]:
    rows = [_display_item_from_profile(item) for item in tuple(items or ())]
    rows.sort(key=profile_display_sort_key)
    return tuple(rows)


def profile_display_sort_key(item: Any) -> tuple[int, int, str, str]:
    # Позиции назначает единый резолвер (profile.ordering); здесь только
    # стабильная сортировка по ним.
    return (
        int(getattr(item, "group_rank", 10_000) or 0),
        int(getattr(item, "order", 0) or 0),
        str(getattr(item, "display_name", "") or "").lower(),
        str(getattr(item, "key", "") or ""),
    )


def _display_item_from_profile(item: Any) -> ProfileDisplayItem:
    order = int(getattr(item, "order", 0) or 0)
    return ProfileDisplayItem(
        key=str(getattr(item, "key", "") or ""),
        persistent_key=str(getattr(item, "persistent_key", "") or ""),
        profile_index=int(getattr(item, "profile_index", -1) or -1),
        display_name=str(getattr(item, "display_name", "") or "").strip() or "Профиль",
        enabled=bool(getattr(item, "enabled", False)),
        in_preset=bool(getattr(item, "in_preset", False)),
        strategy_id=str(getattr(item, "strategy_id", "") or "none"),
        strategy_name=str(getattr(item, "strategy_name", "") or "Стратегия не выбрана"),
        match_lines=tuple(getattr(item, "match_lines", ()) or ()),
        list_type=str(getattr(item, "list_type", "") or ""),
        rating=str(getattr(item, "rating", "") or ""),
        favorite=bool(getattr(item, "favorite", False)),
        group=str(getattr(item, "group", "") or "common"),
        group_name=str(getattr(item, "group_name", "") or getattr(item, "group", "") or "Общие"),
        order=order,
        source_order=int(getattr(item, "source_order", order) or 0),
        group_rank=int(getattr(item, "group_rank", 10_000) or 0),
        group_collapsed=bool(getattr(item, "group_collapsed", False)),
        user_profile_id=str(getattr(item, "user_profile_id", "") or ""),
        strategy_payload_badge=payload_badge_text(getattr(item, "strategy_payload_scopes", ()) or ()),
    )


__all__ = ["ProfileDisplayItem", "build_profile_display_items", "profile_display_sort_key"]
