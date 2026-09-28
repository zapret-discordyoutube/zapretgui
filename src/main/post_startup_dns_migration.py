from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task


def install_dns_address_migration(
    startup_host,
    *,
    dns_feature,
    log_startup_metric,
) -> None:
    """Сразу после запуска меняет на адаптерах старые адреса DNS-провайдеров на новые.

    Задача стоит в очереди «dns» раньше прогрева страницы Network, поэтому
    страница сразу покажет уже исправленные адреса.
    """

    def _run_dns_address_migration() -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            changed_count = len(list(dns_feature.migrate_outdated_dns_addresses() or []))
        except Exception as exc:
            log(f"Замена старых адресов DNS не выполнена: {exc}", "WARNING")
            return
        log_startup_metric("StartupDnsAddressMigrationFinished", f"changed={changed_count}")

    def _start_dns_address_migration() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task("dns", "DnsAddressMigration", _run_dns_address_migration)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start_dns_address_migration,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_dns_address_migration"]
