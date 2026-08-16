from __future__ import annotations

from dns.state import DnsCommandResult, DnsState


def _to_dns_state(data) -> DnsState:
    return DnsState(
        adapters=tuple(data.adapters),
        dns_info=dict(data.dns_info),
        ipv6_available=bool(data.ipv6_available),
        force_dns_enabled=bool(data.force_dns_active),
        doh_supported=bool(getattr(data, "doh_supported", False)),
    )


def apply_dns_on_startup_async(status_callback=None):
    from dns.dns_worker import apply_dns_on_startup_async as _apply_dns_on_startup_async

    return _apply_dns_on_startup_async(status_callback=status_callback)


def get_network_adapters_native():
    from dns.dns_core import get_network_adapters_native as _get_network_adapters_native

    return _get_network_adapters_native()


def normalize_adapter_alias(alias: str) -> str:
    from dns.dns_core import _normalize_alias

    return _normalize_alias(alias)


def get_force_dns_status() -> bool:
    from dns.runtime import get_force_dns_status as _get_force_dns_status

    return _get_force_dns_status()


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


def create_dns_quick_check_worker(request_id: int, *, parent=None):
    from dns.dns_check_worker import DNSQuickCheckWorker

    return DNSQuickCheckWorker(
        request_id,
        run_quick_dns_check=run_quick_dns_check,
        parent=parent,
    )


def run_dns_poisoning_check(*, log_callback=None, should_stop=None) -> dict:
    from dns_checker import DNSChecker

    checker = DNSChecker()
    results = checker.check_dns_poisoning(
        log_callback=log_callback,
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


def run_quick_dns_check():
    from utils.net_resolve import DEFAULT_DNS_TIMEOUT, resolve_ipv4

    from dns.dns_check_plans import DNSQuickCheckPlan

    lines: list[str] = [
        "⚡ БЫСТРАЯ ПРОВЕРКА СИСТЕМНОГО DNS",
        "=" * 45,
        "",
    ]
    test_domains = {
        "YouTube": "www.youtube.com",
        "Discord": "discord.com",
        "Google": "google.com",
        "Cloudflare": "cloudflare.com",
    }

    all_ok = True
    for name, domain in test_domains.items():
        try:
            # gethostbyname без таймаута подвешивал страницу DNS на мёртвом
            # резолвере — здесь ждём результат не дольше дедлайна.
            ip = resolve_ipv4(domain, timeout=DEFAULT_DNS_TIMEOUT)
            if ip:
                lines.append(f"✅ {name} ({domain}): {ip}")
            else:
                lines.append(f"❌ {name} ({domain}): не резолвится")
                all_ok = False
        except Exception as e:
            lines.append(f"❌ {name} ({domain}): Ошибка - {e}")
            all_ok = False

    lines.append("")
    if all_ok:
        lines.append("✅ Все домены резолвятся корректно")
    else:
        lines.append("⚠️ Есть проблемы с резолвингом некоторых доменов")

    return DNSQuickCheckPlan(lines=tuple(lines), enable_save=True)


def check_ipv6_connectivity() -> bool:
    from dns.runtime import detect_ipv6_availability

    return detect_ipv6_availability()


def enable_force_dns(*, include_disconnected: bool = False, adapters: list[str] | None = None) -> DnsCommandResult:
    from dns.runtime import enable_force_dns as _enable_force_dns

    success, ok_count, total, message = _enable_force_dns(
        include_disconnected=include_disconnected,
        adapters=adapters,
    )
    return DnsCommandResult(
        success=bool(success),
        message=str(message or ""),
        affected_count=int(ok_count or 0),
        total_count=int(total or 0),
    )


def disable_force_dns(*, reset_to_auto: bool, adapters: list[str] | None = None) -> DnsCommandResult:
    from dns.runtime import disable_force_dns as _disable_force_dns

    success, message = _disable_force_dns(
        reset_to_auto=reset_to_auto,
        adapters=adapters,
    )
    return DnsCommandResult(success=bool(success), message=str(message or ""))


def flush_dns_cache() -> DnsCommandResult:
    from dns.runtime import flush_dns_cache as _flush_dns_cache

    success, message = _flush_dns_cache()
    return DnsCommandResult(success=bool(success), message=str(message or ""))


def get_dns_state() -> DnsState:
    from dns.runtime import load_page_data as _load_page_data

    return _to_dns_state(_load_page_data())


def load_page_data() -> DnsState:
    return get_dns_state()


def warm_page_data_cache() -> DnsState:
    from dns.runtime import warm_page_data_cache as _warm_page_data_cache

    return _to_dns_state(_warm_page_data_cache())


def consume_warmed_page_data() -> DnsState | None:
    from dns.runtime import consume_warmed_page_data as _consume_warmed_page_data

    data = _consume_warmed_page_data()
    if data is None:
        return None
    return _to_dns_state(data)


def refresh_dns_info(adapter_names: list[str]) -> dict[str, dict[str, list[str]]]:
    from dns.runtime import refresh_dns_info as _refresh_dns_info

    return _refresh_dns_info(adapter_names)


def apply_auto_dns(adapters: list[str]) -> DnsCommandResult:
    from dns.runtime import apply_auto_dns as _apply_auto_dns

    success_count = _apply_auto_dns(adapters)
    total = len(adapters or [])
    return DnsCommandResult(
        success=success_count > 0 or total == 0,
        affected_count=int(success_count or 0),
        total_count=total,
    )


def apply_provider_dns(
    adapters: list[str],
    ipv4: list[str],
    ipv6: list[str],
    *,
    ipv6_available: bool,
) -> DnsCommandResult:
    from dns.runtime import apply_provider_dns as _apply_provider_dns

    success_count = _apply_provider_dns(
        adapters,
        ipv4,
        ipv6,
        ipv6_available=ipv6_available,
    )
    total = len(adapters or [])
    return DnsCommandResult(
        success=success_count > 0 or total == 0,
        affected_count=int(success_count or 0),
        total_count=total,
    )


def apply_custom_dns(adapters: list[str], primary: str, secondary: str | None) -> DnsCommandResult:
    from dns.runtime import apply_custom_dns as _apply_custom_dns

    success_count = _apply_custom_dns(adapters, primary, secondary)
    total = len(adapters or [])
    return DnsCommandResult(
        success=success_count > 0 or total == 0,
        affected_count=int(success_count or 0),
        total_count=total,
    )


def run_connectivity_test(test_hosts: list[tuple[str, str]]) -> list[tuple[str, str, bool]]:
    from utils.windows_icmp import ping_ipv4_host_winapi

    results: list[tuple[str, str, bool]] = []
    for name, host in test_hosts:
        try:
            ping_result = ping_ipv4_host_winapi(
                host,
                count=1,
                timeout_ms=2000,
            )
            results.append((name, host, ping_result.ok))
        except Exception:
            results.append((name, host, False))
    return results
