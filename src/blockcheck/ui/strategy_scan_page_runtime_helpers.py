"""Runtime-helper слой для Strategy Scan page."""

from __future__ import annotations

from ui.accessibility import set_state_text
from ui.fluent_widgets import set_tooltip
from app.ui_texts import tr as tr_catalog


def apply_log_expand_state(
    *,
    blockcheck_feature,
    expanded: bool,
    language: str,
    control_card,
    warning_card,
    results_card,
    log_edit,
    expand_log_btn,
) -> None:
    plan = blockcheck_feature.build_log_expand_plan(
        expanded=expanded,
        language=language,
    )

    control_card.setVisible(plan.control_visible)
    if warning_card is not None:
        warning_card.setVisible(plan.warning_visible)
    results_card.setVisible(plan.results_visible)
    log_edit.setMinimumHeight(plan.log_min_height)
    log_edit.setMaximumHeight(plan.log_max_height)
    expand_log_btn.setText(plan.button_text)


def apply_language_plan_ui(
    *,
    blockcheck_feature,
    language: str,
    log_expanded: bool,
    expand_log_btn,
    log_caption_label,
    protocol_label,
    mode_label,
    mode_combo,
    target_label,
    start_btn,
    stop_btn,
    prepare_support_btn,
    protocol_combo,
    games_scope_label,
    games_scope_combo,
    quick_domain_btn,
) -> None:
    plan = blockcheck_feature.build_language_plan(
        language=language,
        log_expanded=log_expanded,
    )
    # Карточки без шапок: заголовки не выставляются (set_title добавил бы шапку).
    expand_log_btn.setText(plan.expand_log_text)
    if log_caption_label is not None:
        log_caption_label.setText(plan.log_caption)
    for label, text in (
        (protocol_label, plan.protocol_label),
        (mode_label, plan.mode_label),
        (target_label, plan.target_label),
        (games_scope_label, plan.udp_scope_label),
    ):
        if label is not None:
            label.setText(text)
            set_state_text(label, f"Поле подбора стратегии: {text}")
    start_btn.setText(plan.start_text)
    stop_btn.setText(plan.stop_text)
    set_tooltip(
        start_btn,
        tr_catalog(
            "page.blockcheck_public.action.start.description",
            language=language,
            default="Запустить автоматический перебор стратегий обхода DPI для выбранной цели.",
        )
    )
    set_tooltip(
        stop_btn,
        tr_catalog(
            "page.blockcheck_public.action.stop.description",
            language=language,
            default="Остановить текущее сканирование стратегий и вернуть страницу в обычный режим.",
        )
    )
    if prepare_support_btn is not None:
        prepare_support_btn.setText(plan.prepare_support_text)
    for index, text in enumerate(plan.protocol_items):
        protocol_combo.setItemText(index, text)
    if mode_combo is not None:
        for index, text in enumerate(plan.mode_items):
            mode_combo.setItemText(index, text)
    if games_scope_combo is not None:
        for index, text in enumerate(plan.udp_scope_items):
            games_scope_combo.setItemText(index, text)
    if quick_domain_btn is not None:
        quick_domain_btn.setText(plan.quick_domains_text)
        set_tooltip(quick_domain_btn, plan.quick_domains_tooltip)


def set_support_status(label, text: str) -> None:
    if label is None:
        return
    label.setText(str(text or "").strip())
