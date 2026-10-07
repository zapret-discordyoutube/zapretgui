from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os

from log.log import global_logger
from log.run_log_sessions import run_log_sessions


@dataclass(slots=True)
class BlockcheckRunLogState:
    path: str | None
    created: bool


def create_blockcheck_worker(
    *,
    scope: str = "main",
    user_domains: list[str] | None = None,
    parent=None,
):
    from blockcheck.worker import BlockcheckWorker

    return BlockcheckWorker(
        scope=scope,
        user_domains=user_domains,
        start_run_log=start_blockcheck_run_log,
        append_run_log=append_blockcheck_run_log,
        close_run_log=close_blockcheck_run_log,
        remember_run=remember_blockcheck_run,
        check_dns_servers=check_dns_servers,
        parent=parent,
    )


def load_page_initial_state():
    from blockcheck.page_runtime import load_page_initial_state as _load_page_initial_state

    return _load_page_initial_state()


def prepare_support(*, run_log_file: str | None, mode_label: str, extra_domains: list[str]):
    from blockcheck.page_runtime import prepare_support as _prepare_support

    return _prepare_support(
        run_log_file=run_log_file,
        mode_label=mode_label,
        extra_domains=extra_domains,
    )


def run_user_domain_action(action: str, domain: str):
    import blockcheck.page_runtime as blockcheck_page_runtime

    action_name = str(action or "").strip().lower()
    if action_name == "add":
        return blockcheck_page_runtime.add_user_domain(domain)
    if action_name == "remove":
        blockcheck_page_runtime.remove_user_domain(domain)
        return str(domain or "").strip()
    raise ValueError(f"Неизвестное действие домена BlockCheck: {action_name}")


def make_blockcheck_run_log_path(mode: str) -> str:
    from config.runtime_layout import APPLICATION_PATHS

    log_dir = str(APPLICATION_PATHS.logs_dir)
    try:
        active_log = getattr(global_logger, "log_file", None)
        if isinstance(active_log, str) and active_log.strip():
            resolved_dir = os.path.dirname(active_log)
            if resolved_dir:
                log_dir = resolved_dir
    except Exception:
        pass

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    raw_mode = str(mode or "full").strip().lower()
    safe_mode = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in raw_mode) or "full"
    return os.path.join(log_dir, f"blockcheck_run_{ts}_{safe_mode}.log")


def start_blockcheck_run_log(scope: str, extra_domains: list[str]):
    path = make_blockcheck_run_log_path(scope)
    header = (
        f"=== Blockcheck Run Log ({datetime.now():%Y-%m-%d %H:%M:%S}) ===\n"
        f"Scope: {scope}\n"
        f"User domains: {len(extra_domains)}\n"
    )
    if extra_domains:
        header += f"Domains: {', '.join(extra_domains)}\n"
    header += "=" * 60 + "\n\n"
    created = run_log_sessions.start(path, header)
    return BlockcheckRunLogState(path=path if created else None, created=created)


def append_blockcheck_run_log(path: str | None, message: str) -> None:
    run_log_sessions.append(path, message)


def close_blockcheck_run_log(path: str | None) -> None:
    run_log_sessions.close(path)


def check_dns_servers(*, should_stop=None) -> dict:
    """Проверка DNS-серверов для «Полной проверки»: выводы и полный текст одним словарём."""
    from dns import server_check_plans
    from dns.commands import run_server_check

    report = run_server_check(should_stop=should_stop)
    findings = [{"level": str(item.level), "text": str(item.text)} for item in report.findings]
    levels = {item["level"] for item in findings}
    level = next((name for name in ("fail", "warn") if name in levels), "ok" if findings else "unknown")
    return {"level": level, "findings": findings, "text": server_check_plans.build_text_report(report)}


def remember_blockcheck_run(report: dict, log_file: str | None) -> dict:
    """Записывает прогон в историю и рядом с журналом кладёт отчёт в строгом виде.

    Возвращает, что изменилось с прошлой такой же проверки:
    ``{"changes": [...], "previous_time": "...", "json_file": "..."}``.
    """
    from diagnostics import history
    from settings.store import add_check_history_run, get_check_history

    entry = history.blockcheck_entry(report, log_file=str(log_file or ""))
    previous = history.previous_run(get_check_history(), entry)
    runs = add_check_history_run(entry)

    json_file = ""
    if log_file:
        try:
            from config.build_info import APP_VERSION
        except Exception:
            APP_VERSION = ""
        json_file = os.path.splitext(str(log_file))[0] + ".json"
        try:
            with open(json_file, "w", encoding="utf-8") as stream:
                stream.write(history.report_json(report, entry, app_version=str(APP_VERSION)))
        except OSError:
            json_file = ""
    return {
        "changes": history.describe_changes(previous, entry),
        "previous_time": str(previous.get("time") or "") if previous else "",
        "json_file": json_file,
        "history": runs,
    }


def build_quick_target_menu_plan(*, scan_protocol: str, current_value: str):
    from blockcheck.strategy_scan_page_plans import build_quick_target_menu_plan as _build_plan

    return _build_plan(scan_protocol=scan_protocol, current_value=current_value)


def prepare_strategy_scan_support(
    *,
    run_log_file,
    target: str,
    protocol_label: str,
    mode_label: str,
    scan_protocol: str,
):
    from blockcheck.strategy_scan_logs import prepare_support

    return prepare_support(
        run_log_file=run_log_file,
        target=target,
        protocol_label=protocol_label,
        mode_label=mode_label,
        scan_protocol=scan_protocol,
    )


def start_strategy_scan_run_log(
    *,
    target: str,
    mode: str,
    scan_protocol: str,
    udp_games_scope: str,
):
    from blockcheck.strategy_scan_logs import start_run_log

    return start_run_log(
        target=target,
        mode=mode,
        scan_protocol=scan_protocol,
        udp_games_scope=udp_games_scope,
    )


def append_strategy_scan_run_log(path: str | None, message: str) -> None:
    from blockcheck.strategy_scan_logs import append_run_log

    append_run_log(path, message)


def close_strategy_scan_run_log(path: str | None) -> None:
    run_log_sessions.close(path)
