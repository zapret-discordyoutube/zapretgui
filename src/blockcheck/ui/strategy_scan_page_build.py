"""Build-helper основных секций Strategy Scan page."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QHeaderView
from qfluentwidgets import CaptionLabel, FluentIcon

from blockcheck.strategy_scan_page_plans import INTRO_DEFAULT, MODE_ITEMS, PROTOCOL_ITEMS, WARNING_DEFAULT
from ui.fluent_widgets import SemanticNotice, SettingsCard, set_tooltip
from ui.accessibility import set_control_accessibility, set_state_text
from ui.log_limits import BLOCKCHECK_LOG_VIEW_MAX_LINES, apply_text_line_limit
from ui.pages.base_page import ScrollBlockingTextEdit
from ui.widgets.fluent_item_tooltip import install_fluent_item_tooltips


@dataclass(slots=True)
class StrategyScanControlWidgets:
    control_card: object
    intro_label: object
    protocol_label: object
    protocol_combo: object
    games_scope_label: object
    games_scope_combo: object
    mode_label: object
    mode_combo: object
    target_label: object
    target_input: object
    quick_domain_btn: object
    udp_scope_hint_label: object
    warning_notice: object
    progress_bar: object
    status_label: object
    start_btn: object
    stop_btn: object


@dataclass(slots=True)
class StrategyScanResultsWidgets:
    results_card: object
    table: object


@dataclass(slots=True)
class StrategyScanLogWidgets:
    log_card: object
    expand_log_btn: object
    support_status_label: object
    prepare_support_btn: object
    log_edit: object


def build_strategy_scan_control_section(
    *,
    tr_fn,
    combo_cls,
    caption_label_cls,
    body_label_cls,
    progress_bar_cls,
    primary_button_cls,
    push_button_cls,
    line_edit_cls,
    parent,
    on_protocol_changed,
    on_udp_games_scope_changed,
    on_show_quick_domains_menu,
    on_start,
    on_stop,
) -> StrategyScanControlWidgets:
    _ = parent

    def _set_action_accessibility(widget, *, name: str, description: str) -> None:
        set_control_accessibility(widget, name=name, description=description)
        set_state_text(widget, name)

    def _field_label(key: str, default: str):
        label = body_label_cls(tr_fn(key, default))
        set_state_text(label, f"Поле подбора стратегии: {label.text()}")
        return label

    control_card = SettingsCard(tr_fn("page.strategy_scan.control", "Поиск рабочей стратегии"))

    intro_label = body_label_cls(tr_fn("page.strategy_scan.intro", INTRO_DEFAULT))
    intro_label.setWordWrap(True)
    control_card.add_widget(intro_label)

    form = QGridLayout()
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    form.setColumnStretch(1, 1)

    protocol_label = _field_label("page.strategy_scan.protocol", "Что должно заработать:")
    protocol_combo = combo_cls()
    for key, default, value in PROTOCOL_ITEMS:
        protocol_combo.addItem(tr_fn(key, default), userData=value)
    protocol_combo.setCurrentIndex(0)
    protocol_combo.setMinimumWidth(360)
    protocol_combo.currentIndexChanged.connect(on_protocol_changed)
    form.addWidget(protocol_label, 0, 0)
    form.addWidget(protocol_combo, 0, 1, alignment=Qt.AlignmentFlag.AlignLeft)

    target_label = _field_label("page.strategy_scan.target", "Какой сайт проверять:")
    target_row = QHBoxLayout()
    target_row.setSpacing(8)
    target_input = line_edit_cls()
    target_input.setText(tr_fn("page.strategy_scan.target.default", "discord.com"))
    target_input.setPlaceholderText(tr_fn("page.strategy_scan.target.placeholder", "discord.com"))
    _set_action_accessibility(
        target_input,
        name="Цель подбора стратегии",
        description="Введите домен или STUN-цель для подбора стратегии.",
    )
    target_input.setMinimumWidth(260)
    target_input.setFixedHeight(33)
    target_row.addWidget(target_input)

    quick_domain_btn = push_button_cls(
        tr_fn("page.strategy_scan.quick_domains", "Выбрать из списка"),
        icon=FluentIcon.MENU,
    )
    quick_domain_description = tr_fn(
        "page.strategy_scan.quick_domains_hint",
        "Готовые адреса: Discord, YouTube, Telegram и другие",
    )
    set_tooltip(quick_domain_btn, quick_domain_description)
    _set_action_accessibility(
        quick_domain_btn,
        name="Быстрый выбор цели",
        description=quick_domain_description,
    )
    quick_domain_btn.clicked.connect(on_show_quick_domains_menu)
    target_row.addWidget(quick_domain_btn)
    target_row.addStretch(1)
    form.addWidget(target_label, 1, 0)
    form.addLayout(target_row, 1, 1)

    games_scope_label = _field_label("page.strategy_scan.udp_scope", "Какие адреса игр:")
    games_scope_combo = combo_cls()
    games_scope_combo.addItem(
        tr_fn("page.strategy_scan.udp_scope_all", "Все списки адресов (по умолчанию)"),
        userData="all",
    )
    games_scope_combo.addItem(
        tr_fn("page.strategy_scan.udp_scope_games_only", "Только игровые списки"),
        userData="games_only",
    )
    games_scope_combo.setCurrentIndex(0)
    games_scope_combo.setMinimumWidth(260)
    games_scope_combo.currentIndexChanged.connect(on_udp_games_scope_changed)
    form.addWidget(games_scope_label, 2, 0)
    form.addWidget(games_scope_combo, 2, 1, alignment=Qt.AlignmentFlag.AlignLeft)

    mode_label = _field_label("page.strategy_scan.mode", "Насколько тщательно:")
    mode_combo = combo_cls()
    for key, default, value in MODE_ITEMS:
        mode_combo.addItem(tr_fn(key, default), value)
    mode_combo.setCurrentIndex(0)
    mode_combo.setMinimumWidth(360)
    form.addWidget(mode_label, 3, 0)
    form.addWidget(mode_combo, 3, 1, alignment=Qt.AlignmentFlag.AlignLeft)
    control_card.add_layout(form)

    udp_scope_hint_label = caption_label_cls("")
    udp_scope_hint_label.setWordWrap(True)
    control_card.add_widget(udp_scope_hint_label)

    warning_notice = SemanticNotice(tr_fn("page.strategy_scan.warning_text", WARNING_DEFAULT), tone="info")
    control_card.add_widget(warning_notice)

    buttons_row = QHBoxLayout()
    buttons_row.setSpacing(8)
    start_btn = primary_button_cls(
        tr_fn("page.strategy_scan.start", "Найти рабочую стратегию"),
        icon=FluentIcon.SEARCH,
    )
    start_description = tr_fn(
        "page.strategy_scan.action.start.description",
        "Запустить автоматический перебор стратегий обхода DPI для выбранной цели.",
    )
    set_tooltip(start_btn, start_description)
    _set_action_accessibility(
        start_btn,
        name="Начать подбор стратегии",
        description=start_description,
    )
    start_btn.clicked.connect(on_start)
    buttons_row.addWidget(start_btn)

    stop_btn = push_button_cls(
        tr_fn("page.strategy_scan.stop", "Остановить"),
        icon=FluentIcon.CANCEL,
    )
    stop_description = tr_fn(
        "page.strategy_scan.action.stop.description",
        "Остановить текущее сканирование стратегий и вернуть страницу в обычный режим.",
    )
    set_tooltip(stop_btn, stop_description)
    _set_action_accessibility(
        stop_btn,
        name="Остановить подбор стратегии",
        description=stop_description,
    )
    stop_btn.setEnabled(False)
    stop_btn.clicked.connect(on_stop)
    buttons_row.addWidget(stop_btn)

    status_label = caption_label_cls(tr_fn("page.strategy_scan.ready", "Готово к поиску"))
    status_label.setWordWrap(True)
    buttons_row.addSpacing(8)
    buttons_row.addWidget(status_label, 1)
    control_card.add_layout(buttons_row)

    progress_bar = progress_bar_cls()
    progress_bar.setVisible(False)
    progress_bar.setFixedHeight(4)
    progress_bar.setRange(0, 100)
    progress_bar.setValue(0)
    set_control_accessibility(
        progress_bar,
        name="Ход подбора стратегии: не выполняется",
        description="Показывает, что подбор стратегии выполняется.",
    )
    set_state_text(progress_bar, "Ход подбора стратегии: не выполняется")
    control_card.add_widget(progress_bar)

    return StrategyScanControlWidgets(
        control_card=control_card,
        intro_label=intro_label,
        protocol_label=protocol_label,
        protocol_combo=protocol_combo,
        games_scope_label=games_scope_label,
        games_scope_combo=games_scope_combo,
        mode_label=mode_label,
        mode_combo=mode_combo,
        target_label=target_label,
        target_input=target_input,
        quick_domain_btn=quick_domain_btn,
        udp_scope_hint_label=udp_scope_hint_label,
        warning_notice=warning_notice,
        progress_bar=progress_bar,
        status_label=status_label,
        start_btn=start_btn,
        stop_btn=stop_btn,
    )


def build_strategy_scan_results_section(*, tr_fn, table_cls) -> StrategyScanResultsWidgets:
    results_card = SettingsCard(
        tr_fn("page.strategy_scan.results", "Найденные стратегии")
    )

    table = table_cls()
    table.setColumnCount(5)
    headers = [
        "#",
        tr_fn("page.strategy_scan.col_strategy", "Стратегия"),
        tr_fn("page.strategy_scan.col_status", "Результат"),
        tr_fn("page.strategy_scan.col_time", "Ответ, мс"),
        tr_fn("page.strategy_scan.col_action", "Применить"),
    ]
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(table_cls.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(table_cls.SelectionBehavior.SelectRows)
    table.setMinimumHeight(250)
    set_control_accessibility(
        table,
        name="Результаты подбора стратегии",
        description="Таблица со стратегиями, статусом проверки, временем и действием применения.",
    )
    set_state_text(table, "Результаты подбора стратегии: пока нет результатов")
    table.verticalHeader().setVisible(False)
    install_fluent_item_tooltips(table)

    try:
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        table.setColumnWidth(0, 50)
    except Exception:
        pass

    results_card.add_widget(table)
    return StrategyScanResultsWidgets(
        results_card=results_card,
        table=table,
    )


def build_strategy_scan_log_section(*, tr_fn, push_button_cls, parent, on_toggle_log_expand, on_prepare_support) -> StrategyScanLogWidgets:
    def _set_action_accessibility(widget, *, name: str, description: str) -> None:
        set_control_accessibility(widget, name=name, description=description)
        set_state_text(widget, name)

    log_card = SettingsCard(
        tr_fn("page.strategy_scan.log", "Подробный лог")
    )

    expand_log_btn = push_button_cls("Развернуть", icon=FluentIcon.FULL_SCREEN)
    _set_action_accessibility(
        expand_log_btn,
        name="Развернуть лог подбора стратегии",
        description="Разворачивает подробный лог подбора стратегии на странице.",
    )
    expand_log_btn.setMinimumWidth(140)
    expand_log_btn.clicked.connect(on_toggle_log_expand)

    log_header = QHBoxLayout()
    support_status_label = CaptionLabel("")
    support_status_label.setWordWrap(True)
    set_state_text(support_status_label, "Статус обращения по подбору стратегии: нет статуса")
    log_header.addWidget(support_status_label, 1)
    log_header.addStretch()

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
    log_header.addWidget(expand_log_btn)
    log_card.add_layout(log_header)

    log_edit = ScrollBlockingTextEdit()
    set_control_accessibility(
        log_edit,
        name="Подробный лог подбора стратегии",
        description="Здесь появляется подробный текстовый лог подбора стратегии.",
    )
    set_state_text(log_edit, "Подробный лог подбора стратегии: пока нет записей")
    log_edit.setReadOnly(True)
    log_edit.setMinimumHeight(180)
    log_edit.setMaximumHeight(300)
    log_edit.setFont(QFont("Consolas", 9))
    apply_text_line_limit(log_edit, BLOCKCHECK_LOG_VIEW_MAX_LINES)
    log_card.add_widget(log_edit)

    return StrategyScanLogWidgets(
        log_card=log_card,
        expand_log_btn=expand_log_btn,
        support_status_label=support_status_label,
        prepare_support_btn=prepare_support_btn,
        log_edit=log_edit,
    )
