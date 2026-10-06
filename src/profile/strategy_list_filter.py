from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from log.log import log
from profile.strategy_families import (
    strategy_count_text,
    strategy_family,
    strategy_family_keys,
    strategy_sort_key,
)
from profile.strategy_shape import payload_badge_accessible_text, payload_badge_text
from profile.strategy_visuals import describe_strategy_visual


STRATEGY_SELECTED_TEXT = "Выбрана"


@dataclass(frozen=True)
class ProfileStrategyListRow:
    strategy_id: str
    name: str
    status_text: str
    accessible_text: str
    is_current: bool
    visual_icon_name: str
    visual_color: str
    visual_label: str
    visual_description: str
    tooltip_text: str
    # Типы пакетов веток составной стратегии («TLS · HTTP»), пусто — обычная.
    payload_badge: str = ""
    # Группа по способу обхода (profile.strategy_families); пусто — без группы.
    family_key: str = ""
    rating: str = ""
    favorite: bool = False


@dataclass(frozen=True)
class ProfileStrategyListGroup:
    """Заголовок группы стратегий одного способа обхода."""

    key: str
    title: str
    description: str
    icon_name: str
    color: str
    count: int

    def accessible_text(self, *, expanded: bool) -> str:
        state = "развернута" if expanded else "свернута"
        return (
            f"Группа {self.title}, {strategy_count_text(self.count)}, {state}. "
            f"Способ обхода: {self.description}. "
            "Нажмите Enter или Пробел, чтобы свернуть или развернуть группу."
        )


@dataclass(frozen=True)
class ProfileStrategyListPlan:
    # Строки уже стоят по группам: все стратегии одной группы идут подряд.
    rows: tuple[ProfileStrategyListRow, ...]
    visible_count: int
    total_count: int
    current_strategy_id: str
    # Заголовки показываются, только когда групп хотя бы две: один заголовок
    # на весь список ничего не объясняет (так выходит у каталога Zapret 1).
    groups: tuple[ProfileStrategyListGroup, ...] = ()


def build_profile_strategy_list_plan(
    *,
    entries,
    states,
    current_strategy_id: str,
    search_text: str,
) -> ProfileStrategyListPlan:
    entries = dict(entries or {})
    states = dict(states or {})
    current_id = str(current_strategy_id or "none").strip() or "none"
    query = str(search_text or "").strip().lower()
    rows: list[ProfileStrategyListRow] = []

    family_keys = strategy_family_keys(entries)
    sorted_entries = list(entries.items())
    sorted_entries.sort(
        key=lambda pair: strategy_sort_key(family_keys[pair[0]], pair[1], states.get(pair[0]))
    )
    group_counts: dict[str, int] = {}

    for strategy_id, entry in sorted_entries:
        strategy_id = str(strategy_id or "").strip()
        name = str(getattr(entry, "name", "") or strategy_id)
        args = str(getattr(entry, "args", "") or "")
        visual = getattr(entry, "visual", None) or describe_strategy_visual(args)
        visual_label = str(getattr(visual, "label", "") or "")
        visual_description = str(getattr(visual, "description", "") or "")
        visual_search = f"{visual_label} {visual_description}".lower()
        if query and query not in name.lower() and query not in args.lower() and query not in visual_search:
            continue

        payload_badge = payload_badge_text(getattr(entry, "payload_scopes", ()) or ())
        state = states.get(strategy_id)
        is_current = strategy_id == current_id
        family_key = family_keys.get(strategy_id, "other")
        group_counts[family_key] = group_counts.get(family_key, 0) + 1
        status_parts = _strategy_status_parts(state, is_current=is_current, include_unselected=False)
        accessible_status_parts = _strategy_status_parts(state, is_current=is_current, include_unselected=True)
        tooltip_parts = [visual_description.strip(), args]
        rows.append(
            ProfileStrategyListRow(
                strategy_id=strategy_id,
                name=name,
                status_text=" • ".join(status_parts),
                accessible_text=_strategy_screen_reader_text(
                    name=name,
                    status_parts=accessible_status_parts,
                    visual_label=visual_label,
                    visual_description=visual_description,
                    payload_badge=payload_badge,
                ),
                is_current=is_current,
                visual_icon_name=str(getattr(visual, "icon_name", "") or ""),
                visual_color=str(getattr(visual, "color", "") or ""),
                visual_label=visual_label,
                visual_description=visual_description,
                tooltip_text="\n\n".join(part for part in tooltip_parts if part),
                payload_badge=payload_badge,
                family_key=family_key,
                rating=str(getattr(state, "rating", "") or ""),
                favorite=bool(getattr(state, "favorite", False)),
            )
        )

    return ProfileStrategyListPlan(
        rows=tuple(rows),
        visible_count=len(rows),
        total_count=len(entries),
        current_strategy_id=current_id,
        groups=strategy_list_groups(group_counts),
    )


def strategy_list_groups(group_counts: dict[str, int]) -> tuple[ProfileStrategyListGroup, ...]:
    """Заголовки групп в порядке появления строк; пусто, если группа одна."""
    if len(group_counts) < 2:
        return ()
    groups = []
    for family_key, count in group_counts.items():
        family = strategy_family(family_key)
        groups.append(
            ProfileStrategyListGroup(
                key=family.key,
                title=family.title,
                description=family.description,
                icon_name=family.icon_name,
                color=family.color,
                count=int(count),
            )
        )
    return tuple(groups)


class ProfileStrategyListFilterWorker(QThread):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        request_id: int,
        *,
        entries,
        states,
        current_strategy_id: str,
        search_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._request_id = int(request_id)
        self._entries = dict(entries or {})
        self._states = dict(states or {})
        self._current_strategy_id = str(current_strategy_id or "none").strip() or "none"
        self._search_text = str(search_text or "")

    def run(self) -> None:
        try:
            plan = build_profile_strategy_list_plan(
                entries=self._entries,
                states=self._states,
                current_strategy_id=self._current_strategy_id,
                search_text=self._search_text,
            )
        except Exception as exc:
            log(f"ProfileStrategyListFilterWorker: не удалось подготовить список стратегий: {exc}", "ERROR")
            self.failed.emit(self._request_id, str(exc))
            return
        self.loaded.emit(self._request_id, plan)


def _strategy_status_parts(state, *, is_current: bool, include_unselected: bool) -> list[str]:
    status_parts = []
    if is_current:
        status_parts.append(STRATEGY_SELECTED_TEXT)
    elif include_unselected:
        status_parts.append("Не выбрана")
    if bool(getattr(state, "favorite", False)):
        status_parts.append("В избранном")
    rating = str(getattr(state, "rating", "") or "")
    if rating == "work":
        status_parts.append("Работает")
    elif rating == "notwork":
        status_parts.append("Не работает")
    return status_parts


def _strategy_screen_reader_text(
    *,
    name: str,
    status_parts: list[str],
    visual_label: str,
    visual_description: str,
    payload_badge: str = "",
) -> str:
    parts = [str(name or "").strip(), payload_badge_accessible_text(payload_badge)]
    parts.extend(_lower_first(part) for part in status_parts if str(part or "").strip())
    parts.extend(
        str(part or "").strip()
        for part in (visual_label, visual_description)
        if str(part or "").strip()
    )
    text = ", ".join(part for part in parts if part)
    return f"{text}. Нажмите Enter или Пробел, чтобы выбрать стратегию." if text else ""


def _lower_first(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    return value[:1].lower() + value[1:]


__all__ = [
    "STRATEGY_SELECTED_TEXT",
    "ProfileStrategyListFilterWorker",
    "ProfileStrategyListGroup",
    "ProfileStrategyListPlan",
    "ProfileStrategyListRow",
    "build_profile_strategy_list_plan",
    "strategy_list_groups",
]
