from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from folders.defaults import build_default_profile_folders
from profile.display_items import ProfileDisplayItem, build_profile_display_items, profile_display_sort_key
from profile.icons import resolve_profile_icon
from profile.match_filters import is_voice_match, ports_label_from_match_lines, protocol_label_from_match_lines


@dataclass(slots=True)
class ProfileListViewState:
    all_items: tuple[ProfileDisplayItem, ...]
    profile_items: dict[str, ProfileDisplayItem]
    group_expanded: dict[str, bool]
    active_profile_types: set[str]
    search_query: str
    show_only_added: bool
    rows: list[dict[str, Any]]


def build_profile_list_view_state(
    items: tuple[Any, ...],
    *,
    active_profile_types: set[str] | None = None,
    search_query: str = "",
    show_only_added: bool = False,
    group_expanded: dict[str, bool] | None = None,
    folder_state: dict[str, Any] | None = None,
) -> ProfileListViewState:
    display_items = build_profile_display_items(tuple(items or ()))
    display_items = apply_profile_folder_state_to_items(display_items, folder_state)
    active = _normalized_profile_types(active_profile_types)
    normalized_search = _normalized_search_query(search_query)
    next_group_expanded = dict(group_expanded or _initial_group_expanded(display_items))
    for item in display_items:
        next_group_expanded.setdefault(str(item.group or "common"), True)
    return ProfileListViewState(
        all_items=display_items,
        profile_items={item.key: item for item in display_items},
        group_expanded=next_group_expanded,
        active_profile_types=active,
        search_query=normalized_search,
        show_only_added=bool(show_only_added),
        rows=_build_profile_rows_from(
            display_items,
            next_group_expanded,
            active_profile_types=active,
            search_query=normalized_search,
            show_only_added=bool(show_only_added),
        ),
    )


def apply_profile_folder_state_to_items(
    items: tuple[ProfileDisplayItem, ...],
    folder_state: dict[str, Any] | None,
) -> tuple[ProfileDisplayItem, ...]:
    if not isinstance(folder_state, dict):
        return tuple(items or ())

    from profile.ordering import live_items_from_display_items, resolve_profile_order_view

    view = resolve_profile_order_view(live_items_from_display_items(tuple(items or ())), folder_state)
    rank_by_folder = {key: rank for rank, key in enumerate(view.folder_keys)}
    next_items: list[ProfileDisplayItem] = []
    for item in tuple(items or ()):
        persistent_key = str(item.persistent_key or "")
        folder_key = view.folder_by_item.get(persistent_key, str(item.group or "common"))
        next_items.append(
            replace(
                item,
                group=folder_key,
                group_name=view.folder_names.get(folder_key, str(item.group_name or "Общие")),
                order=view.position_by_item.get(persistent_key, int(item.order or 0)),
                group_rank=rank_by_folder.get(folder_key, 10_000),
                group_collapsed=view.collapsed.get(folder_key, False),
            )
        )
    return tuple(sorted(next_items, key=profile_display_sort_key))


def moved_profile_display_items(
    items: tuple[Any, ...],
    source_profile_key: str,
    destination_kind: str,
    destination_profile_key: str = "",
    destination_group_key: str = "",
    *,
    folder_state: dict[str, Any] | None = None,
) -> tuple[Any, ...] | None:
    """Единственная реализация оптимистичного перемещения в UI.

    Строит каноническое представление из отображаемых item-ов и применяет к
    нему ту же `plan_view_move`, что использует персист, поэтому показанный
    порядок всегда совпадает с сохраняемым.
    """
    from folders.ordering import FolderOrderView, plan_view_move

    action_by_kind = {"profile": "before", "profile_after": "after", "end": "end", "folder": "folder"}
    action = action_by_kind.get(str(destination_kind or "").strip())
    if action is None:
        return None
    items = tuple(items or ())
    by_key: dict[str, Any] = {}
    grouped: dict[str, list[Any]] = {}
    for item in items:
        key = str(getattr(item, "key", "") or "")
        if not key:
            continue
        by_key[key] = item
        grouped.setdefault(str(getattr(item, "group", "") or "common"), []).append(item)

    folder_names: dict[str, str] = {}
    collapsed: dict[str, bool] = {}
    rank_by_folder: dict[str, int] = {}
    items_by_folder: dict[str, tuple[str, ...]] = {}
    folder_by_item: dict[str, str] = {}
    for group_key, group_items in grouped.items():
        ordered = tuple(
            str(getattr(item, "key", "") or "")
            for item in sorted(group_items, key=profile_display_sort_key)
        )
        items_by_folder[group_key] = ordered
        for key in ordered:
            folder_by_item[key] = group_key
        first = group_items[0]
        folder_names[group_key] = str(getattr(first, "group_name", "") or "")
        collapsed[group_key] = bool(getattr(first, "group_collapsed", False))
        rank_by_folder[group_key] = min(_item_group_rank(item) for item in group_items)
    if isinstance(folder_state, dict):
        state_folders = folder_state.get("folders")
        if isinstance(state_folders, dict):
            ordered_state_keys = sorted(
                (str(key) for key, folder in state_folders.items() if isinstance(folder, dict)),
                key=lambda key: (
                    _folder_order(state_folders.get(key, {}).get("order")),
                    str(state_folders.get(key, {}).get("name") or key).lower(),
                    key,
                ),
            )
            for rank, key in enumerate(ordered_state_keys):
                folder = state_folders.get(key, {})
                folder_names.setdefault(key, str(folder.get("name") or key))
                collapsed.setdefault(key, bool(folder.get("collapsed", False)))
                rank_by_folder.setdefault(key, rank)
                items_by_folder.setdefault(key, ())

    view = FolderOrderView(
        folder_keys=tuple(sorted(items_by_folder, key=lambda key: (rank_by_folder.get(key, 10_000), key.lower()))),
        folder_names=folder_names,
        collapsed=collapsed,
        items_by_folder=items_by_folder,
        folder_by_item=folder_by_item,
        position_by_item={},
    )
    planned = plan_view_move(
        view,
        action=action,
        source_key=str(source_profile_key or "").strip(),
        destination_key=str(destination_profile_key or "").strip(),
        destination_folder_key=str(destination_group_key or "").strip(),
    )
    if planned is None:
        return None
    target_folder, sequence = planned
    target_name = folder_names.get(target_folder) or group_name_for_key(target_folder, items)
    target_rank = rank_by_folder.get(target_folder, 10_000)
    target_collapsed = collapsed.get(target_folder, False)
    position_by_key = {key: index for index, key in enumerate(sequence)}

    next_items: list[Any] = []
    for item in items:
        key = str(getattr(item, "key", "") or "")
        if key in position_by_key:
            next_items.append(
                _display_item_with(
                    item,
                    group=target_folder,
                    group_name=target_name,
                    order=position_by_key[key],
                    group_rank=target_rank,
                    group_collapsed=target_collapsed,
                )
            )
        else:
            next_items.append(item)
    return tuple(next_items)


def _item_group_rank(item: Any) -> int:
    try:
        return int(getattr(item, "group_rank"))
    except Exception:
        return 10_000


def _display_item_with(item: Any, **changes: Any) -> Any:
    try:
        return replace(item, **changes)
    except Exception:
        from types import SimpleNamespace

        data = dict(getattr(item, "__dict__", {}) or {})
        data.update(changes)
        return SimpleNamespace(**data)


_PROTOCOL_TOOLTIP_NOTES = {
    "TCP": "TCP — обычные соединения с сайтами и сервисами (TLS/HTTP).",
    "TCP/HTTP": "TCP/HTTP — незашифрованный HTTP-трафик (порт 80).",
    "UDP": "UDP — трафик без установления соединения: QUIC, голос, игры.",
    "L7": "L7 — фильтр по протоколу приложения: QUIC, STUN, WireGuard и похожие.",
    "TCP/UDP": "TCP и UDP — профиль покрывает оба вида трафика.",
    "Voice": "Voice — голосовой трафик: Discord Voice, STUN (звонки).",
}

_LIST_TYPE_TOOLTIP_NOTES = {
    "hostlist": "Hostlist — обход действует только для доменов из списка этого профиля.",
    "ipset": (
        "IPset — обход действует по IP-адресам из списка "
        "(когда домен определить нельзя, например для QUIC или голоса)."
    ),
}


# Главное, что стоит знать новичку про счётчик «3 из 5»: это не недоделка.
PROFILES_NOT_ALL_NEEDED_HINT = (
    "Включать все профили не нужно: в пресете уже включено то, что обычно требуется. "
    "Если сайт не открывается, сначала смените стратегию у включённого профиля."
)
# До стольких профилей у каждого своё деление полоски (см. делегат списка).
PROFILE_METER_MAX_SEGMENTS = 8


def profile_group_tooltip(group_name: str, active_count: int, count: int) -> str:
    """Подсказка для названия группы в шапке плитки."""
    return (
        f"Группа «{str(group_name or '').strip()}»: профили одного сайта или сервиса.\n"
        f"Включено {int(active_count)} из {int(count)}. "
        "Нажмите на шапку, чтобы свернуть или развернуть группу."
    )


def profile_group_counter_tooltip(active_count: int, count: int) -> str:
    """Подсказка для счётчика «3 из 5» и полоски рядом с ним."""
    if int(count) <= PROFILE_METER_MAX_SEGMENTS:
        meter = (
            "Каждое деление полоски — один профиль, в том же порядке, что строки ниже. "
            "Закрашенное деление — профиль включён."
        )
    else:
        meter = "Полоска показывает, какая часть профилей группы включена."
    return (
        f"Включено {int(active_count)} из {int(count)} профилей группы.\n"
        f"{meter}\n"
        f"{PROFILES_NOT_ALL_NEEDED_HINT}"
    )


def profile_group_chevron_tooltip(expanded: bool) -> str:
    return "Свернуть группу" if expanded else "Развернуть группу"


def profile_state_tooltip(*, in_preset: bool, enabled: bool, icon_in_header: bool) -> str:
    """Подсказка для точки или значка слева от имени профиля."""
    working = bool(in_preset and enabled)
    if icon_in_header:
        marker = "Закрашенная точка" if working else "Кольцо"
    else:
        marker = "Цветной значок" if in_preset else "Серый значок"
    if working:
        return f"{marker}: профиль включён, его трафик обрабатывается выбранной стратегией."
    if in_preset:
        return f"{marker}: профиль есть в пресете, но выключен. В файле это записано через --skip."
    return (
        f"{marker}: профиля нет в пресете, он ничего не делает. Это нормально.\n"
        f"{PROFILES_NOT_ALL_NEEDED_HINT}"
    )


def profile_strategy_tooltip(
    *,
    in_preset: bool,
    enabled: bool,
    strategy_name: str,
    rating: str = "",
    favorite: bool = False,
) -> str:
    """Подсказка для правого края строки: стратегия и отметки пользователя."""
    if not in_preset:
        return (
            "Профиль не добавлен в пресет.\n"
            "Нажмите на него, чтобы включить и выбрать стратегию, — только если этот сайт у вас не открывается."
        )
    if not enabled:
        return (
            "Профиль выключен: движок его пропускает.\n"
            "Включить его можно в меню по правой кнопке мыши."
        )
    lines = [
        f"Стратегия обхода: {str(strategy_name or '').strip()}.",
        "Если сайт не открывается, нажмите на профиль и попробуйте другую стратегию.",
    ]
    if favorite:
        lines.append("Звезда: стратегия у вас в избранном.")
    if rating == "work":
        lines.append("Галочка: вы отметили, что эта стратегия работает.")
    elif rating == "notwork":
        lines.append("Крестик: вы отметили, что эта стратегия не работает.")
    return "\n".join(lines)


def profile_row_tooltip(item: ProfileDisplayItem) -> str:
    match_lines = tuple(item.match_lines or ())
    lines = [match_summary(item)]
    protocol_note = _PROTOCOL_TOOLTIP_NOTES.get(protocol_label_from_match_lines(match_lines))
    if protocol_note:
        lines.append(protocol_note)
    list_note = _LIST_TYPE_TOOLTIP_NOTES.get(str(item.list_type or "").strip().lower())
    if list_note:
        lines.append(list_note)
    strategy_name = str(item.strategy_name or "").strip()
    if strategy_name and strategy_name != "Стратегия не выбрана":
        lines.append(f"Стратегия обхода: {strategy_name}.")
    return "\n".join(lines)


def row_for_profile(item: ProfileDisplayItem, *, header_icon_name: str = "") -> dict[str, Any]:
    match_lines = tuple(item.match_lines or ())
    ports = ports_label_from_match_lines(match_lines)
    description_parts = [
        part
        for part in (
            protocol_label_from_match_lines(match_lines),
            f"порты: {ports}" if ports else "",
        )
        if part
    ]
    tooltip = profile_row_tooltip(item)
    if not item.in_preset:
        tooltip = (
            f"{tooltip}\nПрофиля ещё нет в пресете. Включать его не обязательно: нажмите на него и выберите "
            "готовую стратегию, только если этот сайт у вас не открывается."
        )
    elif not item.enabled:
        tooltip = f"{tooltip}\nПрофиль есть в пресете, но сейчас выключен. В файле это записано через --skip."
    icon = resolve_profile_icon(item.display_name, match_lines)
    return {
        "kind": "profile",
        "key": item.key,
        "persistent_key": item.persistent_key,
        "display_name": item.display_name,
        "description": " | ".join(description_parts),
        "strategy_id": item.strategy_id,
        "strategy_name": item.strategy_name,
        "strategy_payload_badge": getattr(item, "strategy_payload_badge", ""),
        "match_lines": match_lines,
        "list_type": item.list_type,
        "rating": item.rating,
        "favorite": item.favorite,
        "in_preset": item.in_preset,
        "enabled": item.enabled,
        "group": item.group,
        "group_name": item.group_name,
        "icon_name": icon.icon_name,
        "icon_color": icon.color if item.in_preset else "#888888",
        # Тот же значок уже стоит в шапке плитки — в строке вместо него
        # рисуется точка состояния.
        "icon_in_header": bool(header_icon_name) and icon.icon_name == header_icon_name,
        "tooltip": tooltip,
    }


def build_profile_rows_from(
    items: tuple[ProfileDisplayItem, ...],
    group_expanded: dict[str, bool],
    *,
    active_profile_types: set[str],
    search_query: str,
    show_only_added: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped = grouped_items(items)
    if not grouped:
        return rows
    for group_key in ordered_group_keys(grouped):
        group_items = tuple(
            item
            for item in grouped.get(group_key, ())
            if profile_matches_filter(
                item,
                active_profile_types=active_profile_types,
                search_query=search_query,
                show_only_added=show_only_added,
            )
        )
        if not group_items:
            continue
        group_items = tuple(sorted(group_items, key=profile_display_sort_key))
        group_name = str(group_items[0].group_name or group_key.title())
        expanded = group_expanded.get(group_key, True)
        # Профиль «работает», если он есть в пресете и не выключен через
        # --skip. Плитка показывает это как «3 из 5» и полоской по профилям.
        active_flags = tuple(bool(item.in_preset and item.enabled) for item in group_items)
        shared_icon = _shared_group_icon(group_items)
        rows.append({
            "kind": "folder",
            "group": group_key,
            "group_name": group_name,
            "collapsed": not expanded,
            "count": len(group_items),
            "active_count": sum(active_flags),
            "active_flags": active_flags,
            "tooltip": profile_group_tooltip(group_name, sum(active_flags), len(group_items)),
            "icon_name": shared_icon.icon_name if shared_icon is not None else "",
            "icon_color": shared_icon.color if shared_icon is not None else "",
        })
        if not expanded and not str(search_query or "").strip():
            continue
        header_icon_name = shared_icon.icon_name if shared_icon is not None else ""
        rows.extend(row_for_profile(item, header_icon_name=header_icon_name) for item in group_items)
    return rows


_SITE_ICON_PREFIXES = ("simple:", "fa5b.")
_INITIALS_ICON_PREFIX = "profile-initials:"


def _shared_group_icon(group_items: tuple[ProfileDisplayItem, ...]):
    """Значок группы для шапки плитки — значок её главного сайта.

    В шапку идёт только значок сайта (YouTube, Discord). Служебные значки —
    микрофон звонков, облако хостера — и значки из первых букв имени сайт не
    обозначают и в шапку не выносятся. Сайт считается главным, когда его
    значок носит больше половины профилей группы; профили со служебным
    значком в этот счёт не входят. Профили с другим значком (vencord в группе
    Discord) оставляют его у своей строки.
    """
    counts: dict[str, int] = {}
    specs: dict[str, Any] = {}
    counted_rows = 0
    for item in group_items:
        icon = resolve_profile_icon(item.display_name, tuple(item.match_lines or ()))
        if icon.icon_name.startswith(_SITE_ICON_PREFIXES):
            counts[icon.icon_name] = counts.get(icon.icon_name, 0) + 1
            specs.setdefault(icon.icon_name, icon)
            counted_rows += 1
        elif icon.icon_name.startswith(_INITIALS_ICON_PREFIX):
            counted_rows += 1
    if not counts:
        return None
    icon_name, count = max(counts.items(), key=lambda entry: entry[1])
    if count * 2 <= counted_rows:
        return None
    return specs[icon_name]


def profile_matches_filter(
    item: ProfileDisplayItem,
    *,
    active_profile_types: set[str],
    search_query: str,
    show_only_added: bool = False,
) -> bool:
    if bool(show_only_added) and not bool(getattr(item, "in_preset", False)):
        return False
    return profile_matches_type_filter(item, active_profile_types) and profile_matches_search_query(item, search_query)


def profile_matches_type_filter(item: ProfileDisplayItem, active_profile_types: set[str]) -> bool:
    if "all" in active_profile_types:
        return True
    match_lines = tuple(item.match_lines or ())
    protocol = protocol_label_from_match_lines(match_lines).upper()
    summary = match_summary(item)
    text = f"{item.display_name} {summary} {item.group}".lower()
    if "tcp" in active_profile_types and "TCP" in protocol:
        return True
    if "udp" in active_profile_types and ("UDP" in protocol or "L7" in protocol):
        return True
    if "discord" in active_profile_types and "discord" in text:
        return True
    if "voice" in active_profile_types and is_voice_match(match_lines):
        return True
    if "games" in active_profile_types and "game" in text:
        return True
    return False


def profile_matches_search_query(item: ProfileDisplayItem, search_query: str) -> bool:
    query = str(search_query or "")
    if not query:
        return True
    search_text = profile_search_text(item)
    return all(part in search_text for part in query.split())


def normalized_profile_types(profile_types: set[str] | None) -> set[str]:
    active = {str(value) for value in (profile_types or {"all"}) if str(value or "").strip()}
    if not active:
        active = {"all"}
    return active


def normalized_search_query(query: str | None) -> str:
    return " ".join(str(query or "").strip().lower().split())


def grouped_items(items: tuple[ProfileDisplayItem, ...]) -> dict[str, list[ProfileDisplayItem]]:
    grouped: dict[str, list[ProfileDisplayItem]] = {}
    for item in tuple(items or ()):
        group_key = str(item.group or "common")
        grouped.setdefault(group_key, []).append(item)
    return grouped


def initial_group_expanded(items: tuple[ProfileDisplayItem, ...]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for item in tuple(items or ()):
        group_key = str(item.group or "common")
        result.setdefault(group_key, not bool(item.group_collapsed))
    return result


def ordered_group_keys(grouped: dict[str, list[ProfileDisplayItem]]) -> list[str]:
    # Ранг папки назначает единый резолвер (profile.ordering) из СОХРАНЁННОГО
    # состояния папок; дефолты сюда больше не подмешиваются.
    def group_rank(group_key: str) -> int:
        group_items = grouped.get(group_key) or []
        if not group_items:
            return 10_000
        return min(_item_group_rank(item) for item in group_items)

    return sorted(grouped, key=lambda key: (group_rank(str(key)), str(key).lower()))


def group_name_for_key(group_key: str, items: tuple[ProfileDisplayItem, ...]) -> str:
    key = str(group_key or "").strip() or "common"
    for item in tuple(items or ()):
        if str(item.group or "common") == key and str(item.group_name or "").strip():
            return str(item.group_name or "").strip()
    folder = build_default_profile_folders().get("folders", {}).get(key)
    if isinstance(folder, dict):
        return str(folder.get("name") or key)
    return key.title()


def folder_order(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 10_000


def match_summary(item: ProfileDisplayItem) -> str:
    parts = [
        part
        for part in (
            protocol_label_from_match_lines(tuple(item.match_lines or ())),
            ports_label_from_match_lines(tuple(item.match_lines or ())),
            item.list_type,
        )
        if part
    ]
    return " • ".join(parts) or "без явных условий"


def profile_search_text(item: ProfileDisplayItem) -> str:
    match_lines = tuple(item.match_lines or ())
    parts = [
        item.display_name,
        item.group,
        item.group_name,
        item.strategy_id,
        item.strategy_name,
        item.list_type,
        match_summary(item),
        " ".join(match_lines),
    ]
    return " ".join(str(part or "") for part in parts).lower()


_build_profile_rows_from = build_profile_rows_from
_folder_order = folder_order
_group_name_for_key = group_name_for_key
_grouped_items = grouped_items
_initial_group_expanded = initial_group_expanded
_match_summary = match_summary
_normalized_profile_types = normalized_profile_types
_normalized_search_query = normalized_search_query
_ordered_group_keys = ordered_group_keys
_profile_matches_filter = profile_matches_filter
_profile_matches_search_query = profile_matches_search_query
_profile_matches_type_filter = profile_matches_type_filter
_profile_search_text = profile_search_text
_row_for_profile = row_for_profile


__all__ = [
    "ProfileListViewState",
    "build_profile_list_view_state",
    "build_profile_rows_from",
    "group_name_for_key",
    "grouped_items",
    "initial_group_expanded",
    "moved_profile_display_items",
    "normalized_profile_types",
    "normalized_search_query",
    "ordered_group_keys",
    "profile_matches_filter",
    "profile_matches_search_query",
    "profile_matches_type_filter",
    "profile_search_text",
    "row_for_profile",
]
