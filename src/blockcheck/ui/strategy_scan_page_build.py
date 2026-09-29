"""Сборка вкладки «Подбор стратегии».

Сверху вниз:

1. Карточка «Что починить»: плитки (сайты / голос / игры), цель, тщательность
   и большая кнопка «Найти рабочую стратегию».
2. Панель хода и итога с талисманом (``ScanProgressPanel``).
3. Результаты: найденные стратегии и свёрнутые группы остальных.
4. Строка «Подробный лог» (открывается в отдельном окне) и «Подготовить обращение».
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QHBoxLayout
from qfluentwidgets import CaptionLabel, FluentIcon

from blockcheck.strategy_scan_page_plans import MODE_ITEMS, PROTOCOL_ITEMS, PROTOCOL_TILE_DETAILS
from blockcheck.ui.strategy_scan_widgets import ChoiceRadios, ChoiceTiles, ScanProgressPanel, StrategyResultsView
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard, set_tooltip


@dataclass(slots=True)
class StrategyScanControlWidgets:
    control_card: object
    protocol_label: object
    protocol_combo: object
    games_scope_label: object
    games_scope_combo: object
    mode_label: object
    mode_combo: object
    mode_hint_label: object
    target_label: object
    target_input: object
    quick_domain_btn: object
    udp_scope_hint_label: object
    start_btn: object
    stop_btn: object


@dataclass(slots=True)
class StrategyScanResultsWidgets:
    panel: object
    progress_bar: object
    status_label: object
    results_card: object
    results_view: object


@dataclass(slots=True)
class StrategyScanLogWidgets:
    log_card: object
    log_btn: object
    support_status_label: object
    prepare_support_btn: object


def _set_action_accessibility(widget, *, name: str, description: str) -> None:
    set_control_accessibility(widget, name=name, description=description)
    set_state_text(widget, name)


def build_strategy_scan_control_section(
    *,
    tr_fn,
    combo_cls,
    body_label_cls,
    primary_button_cls,
    push_button_cls,
    line_edit_cls,
    on_protocol_changed,
    on_udp_games_scope_changed,
    on_show_quick_domains_menu,
    on_start,
    on_stop,
) -> StrategyScanControlWidgets:
    def _field_label(key: str, default: str):
        label = body_label_cls(tr_fn(key, default))
        set_state_text(label, f"Поле подбора стратегии: {label.text()}")
        return label

    control_card = SettingsCard()

    protocol_label = _field_label("page.strategy_scan.protocol", "Что должно заработать?")
    control_card.add_widget(protocol_label)
    protocol_combo = ChoiceTiles()
    protocol_combo.set_accessible_group_name("Что должно заработать")
    for (key, default, value), (subtitle_key, subtitle_default, icon) in zip(PROTOCOL_ITEMS, PROTOCOL_TILE_DETAILS):
        protocol_combo.addItem(tr_fn(key, default), userData=value)
        protocol_combo.set_item_details(
            protocol_combo.count() - 1,
            subtitle=tr_fn(subtitle_key, subtitle_default),
            icon=icon,
        )
    protocol_combo.setCurrentIndex(0)
    protocol_combo.currentIndexChanged.connect(on_protocol_changed)
    control_card.add_widget(protocol_combo)

    target_row = QHBoxLayout()
    target_row.setSpacing(10)
    target_label = _field_label("page.strategy_scan.target", "Какой сайт проверить:")
    target_input = line_edit_cls()
    target_input.setText(tr_fn("page.strategy_scan.target.default", "discord.com"))
    target_input.setPlaceholderText(tr_fn("page.strategy_scan.target.placeholder", "discord.com"))
    _set_action_accessibility(
        target_input,
        name="Цель подбора стратегии",
        description="Введите домен или STUN-цель для подбора стратегии.",
    )
    target_input.setMinimumWidth(200)
    target_input.setFixedHeight(33)
    quick_domain_btn = push_button_cls(
        tr_fn("page.strategy_scan.quick_domains", "Выбрать из списка"),
        icon=FluentIcon.MENU,
    )
    quick_domain_description = tr_fn(
        "page.strategy_scan.quick_domains_hint",
        "Готовые адреса: Discord, YouTube, Telegram и другие",
    )
    set_tooltip(quick_domain_btn, quick_domain_description)
    _set_action_accessibility(quick_domain_btn, name="Быстрый выбор цели", description=quick_domain_description)
    quick_domain_btn.clicked.connect(on_show_quick_domains_menu)
    target_row.addWidget(target_label)
    target_row.addWidget(target_input, 1)
    target_row.addWidget(quick_domain_btn)

    # В режиме онлайн-игр вместо сайта выбирается набор адресов игр.
    games_scope_label = _field_label("page.strategy_scan.udp_scope", "Какие адреса игр:")
    games_scope_combo = combo_cls()
    games_scope_combo.addItem(tr_fn("page.strategy_scan.udp_scope_all", "Все списки адресов (по умолчанию)"), userData="all")
    games_scope_combo.addItem(tr_fn("page.strategy_scan.udp_scope_games_only", "Только игровые списки"), userData="games_only")
    games_scope_combo.setCurrentIndex(0)
    games_scope_combo.setMinimumWidth(260)
    games_scope_combo.currentIndexChanged.connect(on_udp_games_scope_changed)
    target_row.addWidget(games_scope_label)
    target_row.addWidget(games_scope_combo)
    target_row.addStretch(0)
    control_card.add_layout(target_row)

    udp_scope_hint_label = CaptionLabel("")
    udp_scope_hint_label.setWordWrap(True)
    control_card.add_widget(udp_scope_hint_label)

    run_row = QHBoxLayout()
    run_row.setSpacing(12)
    mode_label = _field_label("page.strategy_scan.mode", "Тщательность:")
    mode_combo = ChoiceRadios()
    for key, default, value in MODE_ITEMS:
        mode_combo.addItem(tr_fn(key, default), userData=value)
    mode_combo.setCurrentIndex(0)
    mode_hint_label = CaptionLabel("")
    run_row.addWidget(mode_label)
    run_row.addWidget(mode_combo)
    run_row.addWidget(mode_hint_label)
    run_row.addStretch(1)

    start_btn = primary_button_cls(tr_fn("page.strategy_scan.start", "Найти рабочую стратегию"), icon=FluentIcon.SEARCH)
    start_btn.setMinimumHeight(36)
    start_description = tr_fn(
        "page.strategy_scan.action.start.description",
        "Запустить автоматический перебор стратегий обхода DPI для выбранной цели.",
    )
    set_tooltip(start_btn, start_description)
    _set_action_accessibility(start_btn, name="Начать подбор стратегии", description=start_description)
    start_btn.clicked.connect(on_start)
    run_row.addWidget(start_btn)

    stop_btn = push_button_cls(tr_fn("page.strategy_scan.stop", "Остановить"), icon=FluentIcon.CANCEL)
    stop_btn.setMinimumHeight(36)
    stop_description = tr_fn(
        "page.strategy_scan.action.stop.description",
        "Остановить текущее сканирование стратегий и вернуть страницу в обычный режим.",
    )
    set_tooltip(stop_btn, stop_description)
    _set_action_accessibility(stop_btn, name="Остановить подбор стратегии", description=stop_description)
    stop_btn.setEnabled(False)
    stop_btn.setVisible(False)
    stop_btn.clicked.connect(on_stop)
    run_row.addWidget(stop_btn)
    control_card.add_layout(run_row)

    return StrategyScanControlWidgets(
        control_card=control_card,
        protocol_label=protocol_label,
        protocol_combo=protocol_combo,
        games_scope_label=games_scope_label,
        games_scope_combo=games_scope_combo,
        mode_label=mode_label,
        mode_combo=mode_combo,
        mode_hint_label=mode_hint_label,
        target_label=target_label,
        target_input=target_input,
        quick_domain_btn=quick_domain_btn,
        udp_scope_hint_label=udp_scope_hint_label,
        start_btn=start_btn,
        stop_btn=stop_btn,
    )


def build_strategy_scan_results_section(*, on_apply_best) -> StrategyScanResultsWidgets:
    panel = ScanProgressPanel()
    panel.apply_best_clicked.connect(on_apply_best)
    progress_bar = panel.progress_bar
    set_control_accessibility(
        progress_bar,
        name="Ход подбора стратегии: не выполняется",
        description="Показывает, сколько стратегий уже проверено.",
    )
    set_state_text(progress_bar, "Ход подбора стратегии: не выполняется")

    results_card = SettingsCard()
    results_view = StrategyResultsView()
    set_control_accessibility(
        results_view,
        name="Результаты подбора стратегии",
        description="Найденные стратегии и свёрнутые группы остальных проверенных.",
    )
    set_state_text(results_view, "Результаты подбора стратегии: пока нет результатов")
    results_card.add_widget(results_view)
    results_card.setVisible(False)
    return StrategyScanResultsWidgets(
        panel=panel,
        progress_bar=progress_bar,
        status_label=panel.status_label,
        results_card=results_card,
        results_view=results_view,
    )


def build_strategy_scan_log_section(*, tr_fn, push_button_cls, on_open_log, on_prepare_support) -> StrategyScanLogWidgets:
    log_card = SettingsCard()

    log_header = QHBoxLayout()
    log_btn = push_button_cls(tr_fn("page.strategy_scan.log", "Подробный лог"), icon=FluentIcon.DOCUMENT)
    _set_action_accessibility(
        log_btn,
        name="Открыть подробный лог подбора стратегии",
        description="Открывает технический лог подбора в отдельном окне — он нужен для обращения в поддержку.",
    )
    log_btn.clicked.connect(on_open_log)
    log_header.addWidget(log_btn)
    support_status_label = CaptionLabel("")
    support_status_label.setWordWrap(True)
    set_state_text(support_status_label, "Статус обращения по подбору стратегии: нет статуса")
    log_header.addWidget(support_status_label, 1)

    prepare_support_btn = push_button_cls(
        tr_fn("page.strategy_scan.prepare_support", "Подготовить обращение"),
        icon=FluentIcon.GITHUB,
    )
    _set_action_accessibility(
        prepare_support_btn,
        name="Подготовить обращение по подбору стратегии",
        description="Готовит обращение с логами подбора стратегии для поддержки.",
    )
    prepare_support_btn.clicked.connect(on_prepare_support)
    log_header.addWidget(prepare_support_btn)
    log_card.add_layout(log_header)

    return StrategyScanLogWidgets(
        log_card=log_card,
        log_btn=log_btn,
        support_status_label=support_status_label,
        prepare_support_btn=prepare_support_btn,
    )
