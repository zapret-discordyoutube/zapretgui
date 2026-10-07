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


def start_local_proxy(mode: str) -> DnsCommandResult:
    from dns.runtime import start_local_proxy as _start_local_proxy

    return _start_local_proxy(mode)


def stop_local_proxy_if_unused() -> bool:
    from dns.runtime import stop_local_proxy_if_unused as _stop_local_proxy_if_unused

    return _stop_local_proxy_if_unused()


def repair_local_proxy() -> str:
    from dns.runtime import repair_local_proxy as _repair_local_proxy

    return _repair_local_proxy()


def reset_to_auto(adapters: list[str]) -> DnsCommandResult:
    from dns.runtime import reset_to_auto as _reset_to_auto

    return _reset_to_auto(adapters)


def flush_dns_cache() -> DnsCommandResult:
    from dns.runtime import flush_dns_cache as _flush_dns_cache

    return _flush_dns_cache()


def measure_dns_latency(servers: list[str]):
    from dns.latency import measure_dns_latency as _measure_dns_latency

    return _measure_dns_latency(servers)


def build_domain_lookup_servers():
    """Серверы для вкладки «Проверка домена»: системные, шифрованные, из списка программы и свои."""
    from dns.custom_providers import build_dns_providers_with_custom
    from dns.dns_providers import network_providers
    from dns.domain_lookup import (
        EXTRA_SERVERS,
        SERVER_CUSTOM,
        SERVER_DOH,
        SERVER_PROVIDER,
        SERVER_SYSTEM,
        DnsServer,
    )
    from dns.custom_providers import CUSTOM_DNS_CATEGORY
    from utils.dns_reference import REFERENCE_RESOLVERS

    servers: list = []
    try:
        from utils.windows_dns_query import system_dns_servers

        servers.extend(DnsServer(label=address, address=address, kind=SERVER_SYSTEM) for address in system_dns_servers())
    except Exception:
        pass
    servers.extend(
        DnsServer(label=f"{resolver.label} (шифрованный)", address=resolver.address, kind=SERVER_DOH)
        for resolver in REFERENCE_RESOLVERS
    )

    try:
        from settings.store import get_custom_dns_servers

        custom_servers = get_custom_dns_servers()
    except Exception:
        custom_servers = []
    # Режимы встроенного шифрованного DNS — не серверы в сети: у них один адрес 127.0.0.1.
    providers = build_dns_providers_with_custom(network_providers(), custom_servers)
    for category, group in providers.items():
        kind = SERVER_CUSTOM if category == CUSTOM_DNS_CATEGORY else SERVER_PROVIDER
        for name, data in group.items():
            addresses = list(data.get("ipv4") or ()) or list(data.get("ipv6") or ())
            if addresses:
                servers.append(DnsServer(label=str(name), address=str(addresses[0]), kind=kind))
    servers.extend(DnsServer(label=label, address=address, kind=SERVER_PROVIDER) for label, address in EXTRA_SERVERS)
    return tuple(servers)


def run_domain_lookup(target: str, *, use_external: bool = True, on_stage=None, should_stop=None):
    from dns.domain_lookup import run_domain_lookup as _run_domain_lookup

    return _run_domain_lookup(
        target,
        servers=build_domain_lookup_servers(),
        use_external=use_external,
        on_stage=on_stage,
        should_stop=should_stop,
    )


def build_server_check_targets():
    """Адреса для вкладки «DNS-серверы»: все серверы программы и свои, IPv6 — если он есть."""
    from dns.custom_providers import build_dns_providers_with_custom
    from dns.dns_providers import network_providers
    from dns.server_check import build_targets

    try:
        from settings.store import get_custom_dns_servers

        custom_servers = get_custom_dns_servers()
    except Exception:
        custom_servers = []
    try:
        from dns.winapi import internet_route

        ipv6 = bool(internet_route().has_ipv6)
    except Exception:
        ipv6 = False
    return build_targets(build_dns_providers_with_custom(network_providers(), custom_servers), ipv6=ipv6)


def run_server_check(*, on_progress=None, should_stop=None):
    from dns.server_check import run_server_check as _run_server_check

    return _run_server_check(
        build_server_check_targets(),
        on_progress=on_progress,
        should_stop=should_stop,
        bypass=_running_bypass(),
    )


def _running_bypass() -> tuple[str, ...]:
    """Zapret и другие программы обхода, запущенные сейчас: оговорка к результатам проверки."""
    from utils.bypass_tools import bypass_tools_among

    try:
        from settings.mode import ALL_WINWS_EXE_NAME_SET
        from utils.windows_process_probe import iter_process_records_winapi

        names = [str(name or "").lower() for _pid, name in iter_process_records_winapi()]
    except Exception:
        return ()
    zapret = ("Zapret",) if any(name in ALL_WINWS_EXE_NAME_SET for name in names) else ()
    return zapret + bypass_tools_among(names)
