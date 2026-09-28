"""Сборка секций ConnectionTestPage.

Порядок на странице: строка «что проверить → кнопка» → итог по каждому
сервису крупно → строка подробного отчёта (свёрнут) с кнопкой обращения.
Карточки без шапок: страница живёт во вкладке BlockCheck, и лишние заголовки
съедали место.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import QHBoxLayout
from qfluentwidgets import FluentIcon

from diagnostics.ui.components import ConnectionResultsPanel, ScrollBlockingConnectionTextEdit
from ui.accessibility import set_control_accessibility, set_state_text
from ui.fluent_widgets import SettingsCard, set_tooltip
from ui.log_limits import DIAGNOSTICS_LOG_VIEW_MAX_LINES, apply_text_line_limit


@dataclass(slots=True)
class ConnectionControlsWidgets:
    controls_card: object
    test_select_label: object
    test_combo: object
    status_label: object
    progress_bar: object
    start_btn: object
    stop_btn: object


@dataclass(slots=True)
class ConnectionLogWidgets:
    log_card: object
    hint_label: object
    toggle_btn: object
    send_log_btn: object
    result_text: object


def _set_action_accessibility(widget, *, name: str, description: str) -> None:
    set_control_accessibility(widget, name=name, description=description)
    set_state_text(widget, name)


def build_connection_controls(
    *,
    container_layout,
    tr_fn,
    combo_cls,
    body_label_cls,
    caption_label_cls,
    progress_bar_cls,
    primary_button_cls,
    push_button_cls,
    on_start,
    on_stop,
) -> ConnectionControlsWidgets:
    # Без шапки и без отдельного абзаца-вступления: одна строка «что проверить →
    # статус → кнопка». Пояснение живёт в строке статуса, пока проверка не запущена.
    controls_card = SettingsCard()

    row = QHBoxLayout()
    row.setSpacing(12)
    test_select_label = body_label_cls(tr_fn("page.connection.test.select", "Что проверить:"))
    set_state_text(test_select_label, f"Поле диагностики: {test_select_label.text()}")
    row.addWidget(test_select_label)

    test_combo = combo_cls()
    test_combo.setMinimumWidth(200)
    row.addWidget(test_combo)

    status_label = caption_label_cls(
        tr_fn("page.connection.status.ready", "Проверяем так же, как браузер. Обычно 5–15 секунд")
    )
    row.addSpacing(8)
    row.addWidget(status_label, 1)

    progress_bar = progress_bar_cls()
    progress_bar.setVisible(False)
    progress_bar.setFixedWidth(160)
    set_control_accessibility(
        progress_bar,
        name=tr_fn("page.connection.progress.accessible_name", "Ход диагностики соединений"),
        description=tr_fn(
            "page.connection.progress.accessible_description",
            "Показывает, что проверка выполняется.",
        ),
    )
    row.addWidget(progress_bar)

    start_btn = primary_button_cls(tr_fn("page.connection.button.start", "Проверить"))
    start_btn.setIcon(FluentIcon.PLAY)
    start_description = tr_fn(
        "page.connection.action.start.description",
        "Проверить, открываются ли Discord и YouTube и не подменяет ли DNS их адреса.",
    )
    set_tooltip(start_btn, start_description)
    _set_action_accessibility(
        start_btn,
        name=tr_fn("page.connection.action.start.accessible_name", "Запустить диагностический тест"),
        description=start_description,
    )
    start_btn.clicked.connect(on_start)
    row.addWidget(start_btn)

    stop_btn = push_button_cls(tr_fn("page.connection.button.stop", "Остановить"))
    stop_btn.setIcon(FluentIcon.CANCEL)
    stop_description = tr_fn(
        "page.connection.action.stop.description",
        "Останавливает текущий тест, если он уже запущен.",
    )
    set_tooltip(stop_btn, stop_description)
    _set_action_accessibility(
        stop_btn,
        name=tr_fn("page.connection.action.stop.accessible_name", "Остановить диагностический тест"),
        description=stop_description,
    )
    stop_btn.clicked.connect(on_stop)
    stop_btn.setEnabled(False)
    stop_btn.setVisible(False)
    row.addWidget(stop_btn)
    controls_card.add_layout(row)

    container_layout.addWidget(controls_card)
    return ConnectionControlsWidgets(
        controls_card=controls_card,
        test_select_label=test_select_label,
        test_combo=test_combo,
        status_label=status_label,
        progress_bar=progress_bar,
        start_btn=start_btn,
        stop_btn=stop_btn,
    )


def build_connection_results(*, container_layout, content_parent) -> ConnectionResultsPanel:
    panel = ConnectionResultsPanel(content_parent)
    container_layout.addWidget(panel)
    return panel


def build_connection_log_viewer(
    *,
    container_layout,
    tr_fn,
    caption_label_cls,
    push_button_cls,
    on_toggle,
    on_support,
) -> ConnectionLogWidgets:
    log_card = SettingsCard()

    row = QHBoxLayout()
    row.setSpacing(12)
    hint_label = caption_label_cls(
        tr_fn(
            "page.connection.log.hint",
            "Подробный отчёт: адреса, ответы DNS и время ответа серверов. Пригодится поддержке.",
        )
    )
    row.addWidget(hint_label, 1)

    toggle_btn = push_button_cls(tr_fn("page.connection.button.show_log", "Показать отчёт"))
    toggle_btn.setIcon(FluentIcon.DOCUMENT)
    _set_action_accessibility(
        toggle_btn,
        name=tr_fn("page.connection.action.toggle_log.accessible_name", "Показать подробный отчёт"),
        description=tr_fn(
            "page.connection.action.toggle_log.description",
            "Показывает или прячет технический отчёт проверки.",
        ),
    )
    toggle_btn.clicked.connect(on_toggle)
    row.addWidget(toggle_btn)

    send_log_btn = push_button_cls(tr_fn("page.connection.button.send_log", "Подготовить обращение"))
    send_log_btn.setIcon(FluentIcon.SEND)
    support_description = tr_fn(
        "page.connection.action.support.description",
        "Собрать архив логов и открыть готовое обращение в Forgejo Issues.",
    )
    set_tooltip(send_log_btn, support_description)
    _set_action_accessibility(
        send_log_btn,
        name=tr_fn("page.connection.action.support.accessible_name", "Подготовить обращение с логами"),
        description=support_description,
    )
    send_log_btn.clicked.connect(on_support)
    send_log_btn.setEnabled(False)
    row.addWidget(send_log_btn)
    log_card.add_layout(row)

    result_text = ScrollBlockingConnectionTextEdit()
    result_text.setReadOnly(True)
    result_text.setMinimumHeight(320)
    result_text.setVisible(False)
    set_control_accessibility(
        result_text,
        name=tr_fn("page.connection.result.accessible_name", "Результат диагностики соединений"),
        description=tr_fn(
            "page.connection.result.accessible_description",
            "Показывает ход и итог проверки Discord и YouTube.",
        ),
    )
    set_state_text(
        result_text,
        tr_fn(
            "page.connection.result.initial_state",
            "Результат диагностики соединений: диагностика ещё не запускалась",
        ),
    )
    apply_text_line_limit(result_text, DIAGNOSTICS_LOG_VIEW_MAX_LINES)
    log_card.add_widget(result_text)
    container_layout.addWidget(log_card)
    return ConnectionLogWidgets(
        log_card=log_card,
        hint_label=hint_label,
        toggle_btn=toggle_btn,
        send_log_btn=send_log_btn,
        result_text=result_text,
    )
