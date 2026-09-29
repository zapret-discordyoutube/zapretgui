"""Показ хода и результатов подбора на вкладке «Подбор стратегии»."""

from __future__ import annotations

from app.ui_texts import tr as tr_catalog
import blockcheck.strategy_scan_run_workflow as strategy_scan_run_workflow
from ui.accessibility import set_state_text
from ui.fluent_widgets import InfoBarHelper


def apply_strategy_started_progress(
    *,
    blockcheck_feature,
    strategy_name: str,
    index: int,
    total: int,
    result_rows: list[dict],
    progress_bar,
    status_label,
    done_count: int,
) -> None:
    progress_plan = blockcheck_feature.build_progress_plan(
        strategy_name=strategy_name,
        index=index,
        total=total,
        result_rows=result_rows,
    )
    if progress_plan.total > 0:
        progress_bar.setRange(0, progress_plan.total)
    if progress_bar.value() < done_count:
        progress_bar.setValue(done_count)
    set_state_text(progress_bar, "Ход подбора стратегии: выполняется")
    # Строка статуса для экранного чтения; видимый ход — в шагах панели.
    set_state_text(status_label, f"Статус подбора стратегии: {progress_plan.status_text}")


def append_scan_log(*, log_lines, message: str) -> None:
    log_lines.append(str(message or ""))


def apply_phase_change(*, status_label, phase: str) -> None:
    value = str(phase or "").strip()
    if value:
        set_state_text(status_label, f"Статус подбора стратегии: {value}")


def add_strategy_result_row(
    *,
    blockcheck_feature,
    results_view,
    result,
    row_number: int,
    on_apply_strategy,
) -> dict:
    row_plan = blockcheck_feature.build_result_presentation(result, row_number=row_number)
    verdict = str(row_plan.stored_row.get("verdict") or ("working" if result.success else "failed"))
    on_apply = (lambda found=result: on_apply_strategy(found)) if row_plan.can_apply else None
    results_view.add_result(row_plan, verdict, on_apply)
    return row_plan.stored_row


def apply_finished_scan(
    *,
    blockcheck_feature,
    finish_plan,
    reset_ui,
    scan_protocol: str,
    progress_bar,
    status_label,
    set_support_status,
    parent_widget,
    panel=None,
) -> None:
    reset_ui()

    if finish_plan.total_available > 0:
        progress_bar.setRange(0, finish_plan.total_available)
    status_label.setText(finish_plan.status_text)
    set_state_text(status_label, f"Статус подбора стратегии: {finish_plan.status_text}")
    progress_bar.setValue(min(finish_plan.total_count, progress_bar.maximum()))
    set_state_text(progress_bar, "Ход подбора стратегии: не выполняется")

    outcome = getattr(finish_plan, "outcome", None)
    if panel is not None and outcome is not None:
        panel.show_outcome(
            kind=outcome.kind,
            title=outcome.title,
            detail=outcome.detail,
            best_text=outcome.best_text,
            celebrate=outcome.celebrate,
        )

    if finish_plan.support_status_code == "ready_after_error":
        set_support_status(
            tr_catalog(
                "page.blockcheck_public.support_ready_after_error",
                default="Если что-то непонятно — подготовьте обращение, логи приложатся сами",
            )
        )
        return
    if finish_plan.cancelled:
        set_support_status(
            tr_catalog(
                "page.blockcheck_public.support_ready_after_cancel",
                default="Можно подготовить обращение по частичным логам",
            )
        )
        return
    set_support_status(
        tr_catalog(
            "page.blockcheck_public.support_ready",
            default="Можно подготовить обращение по этому подбору",
        )
    )

    # Панель уже всё сказала; всплывашка — только если панели нет (старые вызовы).
    if panel is not None:
        return
    try:
        notification_plan = blockcheck_feature.build_finish_notification_plan(finish_plan, scan_protocol=scan_protocol)
        title = tr_catalog(notification_plan.title_key, default=notification_plan.title_default)
        body = notification_plan.body_text or tr_catalog(notification_plan.body_key, default=notification_plan.body_default)
        if notification_plan.kind == "warning" and notification_plan.title_key:
            InfoBarHelper.warning(parent_widget, title, body)
        elif notification_plan.kind == "success" and notification_plan.title_key:
            InfoBarHelper.success(parent_widget, title, body)
    except Exception:
        pass


def apply_force_stop_status(
    *,
    worker,
    expected_worker,
    status_label,
    set_support_status,
) -> None:
    if expected_worker is None:
        return
    if worker is expected_worker and worker.is_running:
        warning_text = tr_catalog(
            "page.blockcheck_public.stopping_slow",
            default="Остановка занимает больше времени, ждём завершения фонового сканирования...",
        )
        status_label.setText(warning_text)
        set_state_text(status_label, f"Статус подбора стратегии: {warning_text}")
        strategy_scan_run_workflow.record_strategy_scan_force_stop_warning(worker=worker, warning_text=warning_text)
        set_support_status(
            tr_catalog(
                "page.blockcheck_public.support_wait_stop",
                default="Подождите завершения остановки перед новым запуском",
            )
        )
