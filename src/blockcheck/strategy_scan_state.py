from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

@dataclass(slots=True)
class StrategyScanRunLogState:
    # None — журнал завести не удалось.
    path: Path | None


# Что стало с Zapret после «Применить» (см. ui.page_deps.system._ensure_zapret_for_strategy).
STRATEGY_ZAPRET_RESTARTING = "restarting"  # работал — перезапустится с новым пресетом сам
STRATEGY_ZAPRET_STARTED = "started"  # был выключен — запущен
STRATEGY_ZAPRET_FAILED = "failed"  # запустить не удалось
STRATEGY_ZAPRET_OTHER_METHOD = "other_method"  # выбран не Zapret 2 — стратегия ждёт в его пресете
STRATEGY_ZAPRET_SCAN_RUNNING = "scan_running"  # подбор ещё идёт: Zapret не трогаем


@dataclass(slots=True)
class StrategyApplyResult:
    strategy_name: str
    applied_profile: str
    selected_file_name: str
    operation: str
    # Предупреждения про фейки (--blob=) найденной стратегии.
    blob_warnings: tuple[str, ...] = field(default=(), compare=False)


@dataclass(slots=True)
class StrategyScanStartPlan:
    target: str
    scan_protocol: str
    udp_games_scope: str
    mode: str
    status_text: str


@dataclass(slots=True)
class StrategyScanPanelOutcome:
    """Итог подбора для панели: вид, заголовок, объяснение, лучшая стратегия."""

    kind: str
    title: str
    detail: str = ""
    best_text: str = ""
    # Номер лучшей стратегии в строках результатов (-1 — нет).
    best_index: int = -1
    celebrate: bool = False


@dataclass(slots=True)
class StrategyScanFinishPlan:
    total_available: int
    working_count: int
    total_count: int
    cancelled: bool
    baseline_accessible: bool
    status_text: str
    support_status_code: str
    fatal_error: str = ""
    outcome: StrategyScanPanelOutcome | None = None


@dataclass(slots=True)
class StrategyScanProgressPlan:
    total: int
    status_text: str


@dataclass(slots=True)
class StrategyScanResultPresentation:
    number_text: str
    strategy_name: str
    strategy_tooltip: str
    status_text: str
    status_tone: str
    status_tooltip: str
    time_text: str
    can_apply: bool
    stored_row: dict[str, object]


@dataclass(slots=True)
class StrategyScanProtocolUiPlan:
    scan_protocol: str
    is_udp_games: bool
    show_target_controls: bool
    normalized_target: str
    placeholder_text: str
    # Заметка «проверяйте сайты по одному» нужна только там, где проверяется сайт.
    show_one_site_hint: bool


@dataclass(slots=True)
class StrategyScanUdpHintPlan:
    visible: bool
    text: str
    tooltip: str


@dataclass(slots=True)
class StrategyScanQuickMenuPlan:
    options: list[str]
    current_value: str


@dataclass(slots=True)
class StrategyScanInteractionPlan:
    start_enabled: bool
    stop_enabled: bool
    protocol_enabled: bool
    games_scope_enabled: bool
    mode_enabled: bool
    target_enabled: bool
    quick_domain_enabled: bool


@dataclass(slots=True)
class StrategyScanLanguagePlan:
    log_button_text: str
    protocol_label: str
    target_label: str
    mode_label: str
    mode_items: list[str]
    start_text: str
    stop_text: str
    prepare_support_text: str
    protocol_items: list[str]
    protocol_hints: list[str]
    udp_scope_label: str
    udp_scope_items: list[str]
    quick_domains_text: str
    quick_domains_tooltip: str
    one_site_hint: str


@dataclass(slots=True)
class StrategyScanUiMessagePlan:
    kind: str
    title_key: str
    title_default: str
    body_text: str
    status_text: str = ""


@dataclass(slots=True)
class StrategyScanSelectionState:
    scan_protocol: str
    udp_games_scope: str
    mode: str


@dataclass(slots=True)
class StrategyScanSupportContext:
    scan_protocol: str
    target: str
    protocol_label: str
    mode_label: str
