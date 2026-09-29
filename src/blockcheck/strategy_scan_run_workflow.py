"""Workflow запуска и остановки подбора стратегии BlockCheck."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class StrategyScanRunStartResult:
    worker: object
    target: str
    scan_protocol: str
    udp_games_scope: str
    mode: str
    status_text: str


@dataclass(frozen=True)
class StrategyScanResumeInfo:
    """Что уже проверено для цели, которую собираются подбирать."""

    target: str
    tested_count: int


def plan_strategy_scan_resume(
    *,
    blockcheck_feature,
    raw_target_input: str,
    raw_protocol_value,
    raw_udp_scope_value,
    mode_index: int,
) -> StrategyScanResumeInfo:
    """Сколько стратегий уже не сработало на этой цели — есть ли что продолжать."""
    selection = blockcheck_feature.build_selection_state(
        protocol_value=raw_protocol_value,
        udp_scope_value=raw_udp_scope_value,
        mode_index=mode_index,
    )
    start_plan = blockcheck_feature.plan_scan_start(
        raw_target_input=raw_target_input,
        scan_protocol=selection.scan_protocol,
        udp_games_scope=selection.udp_games_scope,
        mode=selection.mode,
        starting_status_text="",
    )
    tested = blockcheck_feature.count_resumable_strategies(
        target=start_plan.target,
        scan_protocol=start_plan.scan_protocol,
        udp_games_scope=start_plan.udp_games_scope,
    )
    return StrategyScanResumeInfo(target=start_plan.target, tested_count=int(tested or 0))


def start_strategy_scan_run(
    *,
    blockcheck_feature,
    create_strategy_scan_worker,
    raw_target_input: str,
    raw_protocol_value,
    raw_udp_scope_value,
    mode_index: int,
    starting_status_text: str,
    parent,
    on_run_log_started,
    on_strategy_started,
    on_strategy_result,
    on_strategy_args_started=None,
    on_stage_changed=None,
    on_log,
    on_phase_changed,
    on_continue_question,
    on_finished,
    from_start: bool = False,
) -> StrategyScanRunStartResult:
    """Готовит состояние и worker подбора стратегии."""
    selection = blockcheck_feature.build_selection_state(
        protocol_value=raw_protocol_value,
        udp_scope_value=raw_udp_scope_value,
        mode_index=mode_index,
    )
    start_plan = blockcheck_feature.plan_scan_start(
        raw_target_input=raw_target_input,
        scan_protocol=selection.scan_protocol,
        udp_games_scope=selection.udp_games_scope,
        mode=selection.mode,
        starting_status_text=starting_status_text,
    )

    worker = create_strategy_scan_worker(
        target=start_plan.target,
        mode=start_plan.mode,
        scan_protocol=start_plan.scan_protocol,
        udp_games_scope=start_plan.udp_games_scope,
        from_start=bool(from_start),
        parent=None,
    )
    worker.run_log_started.connect(on_run_log_started)
    worker.strategy_started.connect(on_strategy_started)
    if on_strategy_args_started is not None:
        worker.strategy_args_started.connect(on_strategy_args_started)
    if on_stage_changed is not None:
        worker.stage_changed.connect(on_stage_changed)
    worker.strategy_result.connect(on_strategy_result)
    worker.scan_log.connect(on_log)
    worker.phase_changed.connect(on_phase_changed)
    worker.continue_question.connect(on_continue_question)
    worker.scan_finished.connect(on_finished)

    return StrategyScanRunStartResult(
        worker=worker,
        target=start_plan.target,
        scan_protocol=start_plan.scan_protocol,
        udp_games_scope=start_plan.udp_games_scope,
        mode=start_plan.mode,
        status_text=start_plan.status_text,
    )


def start_strategy_scan_worker(worker, *, parent, run_runtime) -> None:
    """Запускает уже подготовленный worker подбора стратегии."""
    run_runtime.start_qobject_worker(
        parent=parent,
        worker_factory=lambda _request_id: worker,
    )


def record_strategy_scan_force_stop_warning(
    *,
    worker,
    warning_text: str,
) -> None:
    """Записывает предупреждение о долгой остановке подбора стратегии."""
    if worker is None:
        return
    try:
        worker.record_run_log_message(f"WARNING: {warning_text}")
    except Exception:
        pass


def request_strategy_scan_stop(
    *,
    worker,
    schedule_stop_check: Callable[[object | None], None],
) -> None:
    """Запрашивает остановку worker-а подбора стратегии.

    ``worker.stop()`` только ставит флаг и обрывает сетевые проверки: процесс
    winws2 гасит сам поток подбора, поэтому окно не ждёт.
    """
    expected_worker = None
    if worker is not None:
        worker.stop()
        expected_worker = worker
    schedule_stop_check(expected_worker)
