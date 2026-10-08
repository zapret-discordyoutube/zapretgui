from __future__ import annotations

from datetime import datetime
import os

from log.log import global_logger
from log.run_log_sessions import run_log_sessions


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
        report_path=make_blockcheck_report_path,
        save_report=save_blockcheck_report,
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


def make_blockcheck_report_path(mode: str) -> str:
    """Куда ляжет отчёт проверки. Файл один — ``.json``: в нём и разбираемые данные, и текст отчёта."""
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
    return os.path.join(log_dir, f"blockcheck_run_{ts}_{safe_mode}.json")


def save_blockcheck_report(report: dict, path: str | None, *, entry: dict | None = None) -> str:
    """Пишет отчёт проверки в ``path``. Возвращает путь или пустую строку, если записать не вышло.

    Так сохраняется и законченная проверка, и прерванная (остановили, упала): во
    втором случае в файле то, что успели узнать, и текст отчёта до этого места.
    """
    from diagnostics import history

    if not path:
        return ""
    try:
        from config.build_info import APP_VERSION
    except Exception:
        APP_VERSION = ""
    entry = entry or {"time": datetime.now().isoformat(timespec="seconds"), "title": "", "level": "unknown"}
    try:
        os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(history.report_json(report, entry, app_version=str(APP_VERSION)))
    except OSError:
        return ""
    return str(path)


def load_past_blockcheck_report(log_file: str | None) -> dict | None:
    """Сохранённый отчёт прошлой проверки. ``None`` — файла нет или он не читается.

    ``log_file`` — путь из записи истории. У новых проверок это сам ``.json``; у
    старых — текстовый журнал, рядом с которым лежит отчёт под тем же именем.
    """
    import json

    from diagnostics.history import REPORT_FORMAT

    if not log_file:
        return None
    try:
        with open(os.path.splitext(str(log_file))[0] + ".json", encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("format") != REPORT_FORMAT:
        return None
    report = document.get("report")
    return report if isinstance(report, dict) else None


def _finding_record(finding) -> dict:
    """Находка словарём: фраза целиком и она же готовыми частями — заголовок, все серверы, пояснение."""
    record = {"level": str(finding.level), "text": str(finding.text)}
    if finding.title:
        record["title"] = str(finding.title)
        record["servers"] = [[str(name), str(address)] for name, address in finding.servers]
        record["note"] = str(finding.note)
    return record


def check_dns_servers(*, should_stop=None) -> dict:
    """Проверка DNS-серверов для «Полной проверки»: выводы и полный текст одним словарём."""
    from dns import server_check_plans
    from dns.commands import run_server_check

    report = run_server_check(should_stop=should_stop)
    findings = [_finding_record(item) for item in report.findings]
    levels = {item["level"] for item in findings}
    level = next((name for name in ("fail", "warn") if name in levels), "ok" if findings else "unknown")
    return {"level": level, "findings": findings, "text": server_check_plans.build_text_report(report)}


def remember_blockcheck_run(report: dict, log_file: str | None, *, preset: str = "") -> dict:
    """Записывает прогон в историю и сохраняет его отчёт в файл ``log_file`` (``.json``).

    ``preset`` — название выбранного пресета. В отчёт дописывается, с каким пресетом шла
    проверка и что показало сравнение с прошлой проверкой в противоположном состоянии
    Zapret (``report["compare"]``, см. ``diagnostics.compare``).

    Возвращает, что изменилось с прошлой такой же проверки:
    ``{"changes": [...], "previous_time": "...", "json_file": "...", "lines": [...]}``;
    ``lines`` — строки, дописанные к тексту отчёта.
    """
    from diagnostics import compare, history
    from settings.store import add_check_history_run, get_check_history

    entry = history.blockcheck_entry(report, log_file=str(log_file or ""), preset=preset)
    past = get_check_history()
    previous = history.previous_run(past, entry)
    changes = history.describe_changes(previous, entry)
    previous_time = str(previous.get("time") or "") if previous else ""
    report["preset"] = str(preset or "")
    report["compare"] = compare.compare_runs(entry, compare.counterpart(past, entry))
    lines = compare.lines(report["compare"], format_time=history.format_time)
    if changes:
        lines += ["", f"🕘 С прошлой проверки ({history.format_time(previous_time)}) {'; '.join(changes)}."]
    # Текст отчёта лежит в том же файле: дописанное должно попасть и в него.
    report["text"] = [*(report.get("text") or ()), *lines]
    runs = add_check_history_run(entry)

    json_file = save_blockcheck_report(report, os.path.splitext(str(log_file))[0] + ".json", entry=entry) if log_file else ""
    return {
        "changes": changes,
        "previous_time": previous_time,
        "json_file": json_file,
        "history": runs,
        "lines": lines,
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
