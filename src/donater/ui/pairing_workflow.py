"""Helper-слой workflow для Premium pairing и информации об устройстве."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QApplication

import donater.ui.page_plans as premium_page_plans
from donater.ui.accessibility import apply_premium_button_accessibility, apply_premium_pair_code_accessibility
from ui.accessibility import set_state_text


def apply_device_info_snapshot_labels(
    *,
    snapshot,
    tr: Callable[[str, str], str],
    device_id_label,
    saved_key_label,
    last_check_label,
) -> None:
    plan = premium_page_plans.build_device_info_plan(
        device_id=snapshot.get("device_id"),
        device_token=snapshot.get("device_token"),
        pair_code=snapshot.get("pair_code"),
        last_check=snapshot.get("last_check"),
        token_present_text=tr("page.premium.label.device_token.present", "device token: ✅"),
        token_absent_text=tr("page.premium.label.device_token.absent", "device token: ❌"),
        pair_template_text=tr("page.premium.label.pair_code.value", "pair: {pair_code}"),
    )

    device_id_text = tr(
        plan.device_id_text_key,
        plan.device_id_text_default,
        **plan.device_id_kwargs,
    )
    device_id_label.setText(device_id_text)
    set_state_text(device_id_label, device_id_text)
    saved_key_label.setText(plan.saved_key_text)
    set_state_text(
        saved_key_label,
        _device_token_accessible_text(
            has_device_token=bool(snapshot.get("device_token")),
            pair_code=str(snapshot.get("pair_code") or "").strip(),
        ),
    )
    last_check_text = tr(
        plan.last_check_text_key,
        plan.last_check_text_default,
        **plan.last_check_kwargs,
    )
    last_check_label.setText(last_check_text)
    set_state_text(last_check_label, _last_check_accessible_text(last_check_text))


def _device_token_accessible_text(*, has_device_token: bool, pair_code: str) -> str:
    parts = ["Токен устройства: есть" if has_device_token else "Токен устройства: не найден"]
    if pair_code:
        parts.append(f"Код привязки: {pair_code}")
    return ". ".join(parts)


def _last_check_accessible_text(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("Последняя проверка:"):
        return value.replace("Последняя проверка:", "Последняя проверка Premium:", 1)
    if value:
        return f"Последняя проверка Premium: {value}"
    return "Последняя проверка Premium: —"


def apply_pair_code_start_ui(
    *,
    activate_btn,
    key_input,
    tr: Callable[[str, str], str],
    set_activation_status: Callable[..., None],
):
    plan = premium_page_plans.build_pair_code_start_plan()
    if plan.clear_key_input:
        key_input.clear()
    apply_premium_pair_code_accessibility(tr_fn=tr, key_input=key_input)
    activate_btn.setEnabled(plan.activate_enabled)
    activate_btn.setText(
        tr(plan.activate_text_key, plan.activate_text_default)
    )
    apply_premium_button_accessibility(
        tr_fn=tr,
        activate_btn=activate_btn,
        activate_loading=plan.activation_in_progress,
    )
    set_activation_status(
        text=plan.activation_status_plan.text,
        text_key=plan.activation_status_plan.text_key,
        text_default=plan.activation_status_plan.text_default,
        text_kwargs=plan.activation_status_plan.text_kwargs,
    )
    return plan


def apply_pair_code_result_ui(
    result,
    *,
    activate_btn,
    key_input,
    tr: Callable[[str, str], str],
    set_activation_status: Callable[..., None],
    update_device_info: Callable[[], None],
    notify_pairing_started: Callable[[], None],
):
    plan = premium_page_plans.build_pair_code_result_plan(result)
    activate_btn.setEnabled(plan.activate_enabled)
    activate_btn.setText(tr(plan.activate_text_key, plan.activate_text_default))
    apply_premium_button_accessibility(
        tr_fn=tr,
        activate_btn=activate_btn,
        activate_loading=plan.activation_in_progress,
    )
    if plan.clear_key_input:
        key_input.clear()
    else:
        key_input.setText(plan.key_input_text)
    apply_premium_pair_code_accessibility(tr_fn=tr, key_input=key_input)
    if plan.copy_to_clipboard and plan.key_input_text:
        try:
            QApplication.clipboard().setText(plan.key_input_text)
        except Exception:
            pass
    set_activation_status(
        text=plan.activation_status_plan.text,
        text_key=plan.activation_status_plan.text_key,
        text_default=plan.activation_status_plan.text_default,
        text_kwargs=plan.activation_status_plan.text_kwargs,
    )
    if plan.update_device_info:
        update_device_info()
    if plan.pairing_started:
        notify_pairing_started()
    return plan


def apply_pair_code_error_ui(
    error,
    *,
    activate_btn,
    key_input,
    tr: Callable[[str, str], str],
    set_activation_status: Callable[..., None],
    update_device_info: Callable[[], None],
):
    plan = premium_page_plans.build_pair_code_error_plan(str(error or ""))
    if plan.clear_key_input:
        key_input.clear()
    apply_premium_pair_code_accessibility(tr_fn=tr, key_input=key_input)
    activate_btn.setEnabled(plan.activate_enabled)
    activate_btn.setText(tr(plan.activate_text_key, plan.activate_text_default))
    apply_premium_button_accessibility(
        tr_fn=tr,
        activate_btn=activate_btn,
        activate_loading=plan.activation_in_progress,
    )
    set_activation_status(
        text=plan.activation_status_plan.text,
        text_key=plan.activation_status_plan.text_key,
        text_default=plan.activation_status_plan.text_default,
        text_kwargs=plan.activation_status_plan.text_kwargs,
    )
    if plan.update_device_info:
        update_device_info()
    return plan
