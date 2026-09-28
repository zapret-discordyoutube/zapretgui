from __future__ import annotations

from dataclasses import dataclass, field

from .strategy_catalog import StrategyEntry
from .strategy_state import ProfileStrategyState


@dataclass(frozen=True)
class ProfileListFileEditorState:
    kind: str = ""
    display_path: str = ""
    text: str = ""
    base_text: str = ""
    user_text: str = ""
    base_display_path: str = ""
    user_display_path: str = ""
    editable: bool = False
    invalid_lines: tuple[tuple[int, str], ...] = ()
    error_text: str = ""
    base_entries_count: int = 0
    user_entries_count: int = 0


@dataclass(frozen=True)
class ProfileListItem:
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
    # Исходный порядок из файла пресета/шаблонов — вход резолвера порядка.
    source_order: int = 0
    group_rank: int = 10_000
    group_collapsed: bool = False
    user_profile_id: str = ""
    profile_name: str = ""
    # Типы пакетов веток составной стратегии (значок «TLS · HTTP»), пусто — обычная.
    strategy_payload_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProfileListPayload:
    items: tuple[ProfileListItem, ...]
    selected_preset_file_name: str
    selected_preset_name: str


@dataclass(frozen=True)
class ProfileSetupPayload:
    item: ProfileListItem
    strategy_entries: dict[str, StrategyEntry]
    strategy_states: dict[str, ProfileStrategyState]
    raw_profile_text: str
    raw_strategy_text: str
    match_summary: str
    match_tab_text: str = ""
    editable_filter_kind: str = ""
    editable_filter_value: str = ""
    editable_filter_enabled: bool = True
    editable_filter_role: str = "primary"
    editable_filter_kinds: tuple[str, ...] = ()
    in_range: str = "x"
    out_range: str = "a"
    current_strategy_state: ProfileStrategyState = ProfileStrategyState()
    # Общие строки пресета (до первого профиля): редактор текста профиля берёт
    # из них объявленные фейки и подключённые lua-файлы для проверки.
    preset_preamble_text: str = ""


@dataclass(frozen=True)
class StrategyApplyResult:
    status: str
    profile_key: str = ""
    strategy_id: str = ""
    should_reload: bool = False
    message: str = ""
    change_kind: str = "unchanged"
    list_structure_changed: bool = False
    profile_payload_changed: bool = False
    profile_list_item_changed: bool = False
    summary_changed: bool = False
    runtime_apply_needed: bool = False
    # Предупреждения про фейки (--blob=) выбранной стратегии: реестр
    # недоступен или стратегия ссылается на фейк, которого нет нигде.
    blob_warnings: tuple[str, ...] = field(default=(), compare=False)
