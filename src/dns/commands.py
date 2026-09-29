from __future__ import annotations

from dns.state import DnsCommandResult, DnsState


def migrate_outdated_dns_addresses():
    from dns.address_migration import migrate_outdated_dns_addresses as _migrate

    return _migrate()


def is_isp_dns_warning_shown() -> bool:
    from settings.store import get_isp_dns_info_shown

    return bool(get_isp_dns_info_shown())


def mark_isp_dns_warning_shown() -> bool:
    from settings.store import set_isp_dns_info_shown

    return bool(set_isp_dns_info_shown(True))


def create_dns_check_worker():
    from dns.dns_check_worker import DNSCheckWorker

    return DNSCheckWorker(run_dns_poisoning_check=run_dns_poisoning_check)


def create_dns_check_save_worker(request_id: int, *, file_path: str, plain_text: str, parent=None):
    from dns.dns_check_worker import DNSCheckSaveWorker

    return DNSCheckSaveWorker(
        request_id,
        file_path=file_path,
        plain_text=plain_text,
        save_dns_check_results=save_dns_check_results,
        parent=parent,
    )


def run_dns_poisoning_check(*, log_callback=None, should_stop=None) -> dict:
    from diagnostics.engine import run_dns_check

    results = run_dns_check(
        emit=log_callback or (lambda _line: None),
        should_stop=should_stop,
    )
    return dict(results or {})


def save_dns_check_results(*, file_path: str, plain_text: str):
    import os
    from datetime import datetime

    from dns.dns_check_plans import DNSSaveResultPlan

    target_path = str(file_path or "").strip()
    if not target_path:
        return DNSSaveResultPlan(
            success=False,
            title="Ошибка",
            content="Не указан путь для сохранения файла.",
        )

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write("DNS CHECK RESULTS\n")
            f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 60 + "\n\n")
            f.write(str(plain_text or ""))

        folder = os.path.dirname(target_path) or None
        if folder and hasattr(os, "startfile"):
            try:
                os.startfile(folder)  # type: ignore[attr-defined]
            except Exception:
                folder = None

        return DNSSaveResultPlan(
            success=True,
            title="Сохранено",
            content=f"Результаты сохранены в:\n{target_path}",
        )
    except Exception as e:
        return DNSSaveResultPlan(
            success=False,
            title="Ошибка",
            content=f"Не удалось сохранить файл:\n{str(e)}",
        )


def load_state() -> DnsState:
    from dns.runtime import load_state as _load_state

    return _load_state()


def warm_state() -> DnsState:
    from dns.runtime import warm_state as _warm_state

    return _warm_state()


def consume_warmed_state() -> DnsState | None:
    from dns.runtime import consume_warmed_state as _consume_warmed_state

    return _consume_warmed_state()


def apply_dns(adapters: list[str], ipv4: list[str], ipv6: list[str]) -> DnsCommandResult:
    from dns.runtime import apply_dns as _apply_dns

    return _apply_dns(adapters, ipv4, ipv6)


def reset_to_auto(adapters: list[str]) -> DnsCommandResult:
    from dns.runtime import reset_to_auto as _reset_to_auto

    return _reset_to_auto(adapters)


def flush_dns_cache() -> DnsCommandResult:
    from dns.runtime import flush_dns_cache as _flush_dns_cache

    return _flush_dns_cache()


def measure_dns_latency(servers: list[str]):
    from dns.latency import measure_dns_latency as _measure_dns_latency

    return _measure_dns_latency(servers)
