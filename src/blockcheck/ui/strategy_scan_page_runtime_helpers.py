"""Смена языка на вкладке «Подбор стратегии»."""

from __future__ import annotations

from app.ui_texts import tr as tr_catalog
from ui.accessibility import set_state_text
from ui.fluent_widgets import set_tooltip


def apply_language_plan_ui(
    *,
    blockcheck_feature,
    language: str,
    log_btn,
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
    plan = blockcheck_feature.build_language_plan(language=language)
    log_btn.setText(plan.log_button_text)
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
        ),
    )
    set_tooltip(
        stop_btn,
        tr_catalog(
            "page.blockcheck_public.action.stop.description",
            language=language,
            default="Остановить текущее сканирование стратегий и вернуть страницу в обычный режим.",
        ),
    )
    if prepare_support_btn is not None:
        prepare_support_btn.setText(plan.prepare_support_text)
    for index, text in enumerate(plan.protocol_items):
        protocol_combo.setItemText(index, text)
    set_details = getattr(protocol_combo, "set_item_details", None)
    if set_details is not None:
        from blockcheck.strategy_scan_page_plans import PROTOCOL_TILE_DETAILS

        for index, hint in enumerate(plan.protocol_hints):
            icon = PROTOCOL_TILE_DETAILS[index][2] if index < len(PROTOCOL_TILE_DETAILS) else "fa5s.circle"
            set_details(index, subtitle=hint, icon=icon)
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
