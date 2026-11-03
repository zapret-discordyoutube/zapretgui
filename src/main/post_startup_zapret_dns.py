from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after

# Как часто спрашивать шлюз проб, не пора ли. Сам интервал решает шлюз:
# utils.probe_gate.ZAPRET_DNS_ADDRESS (сутки).
ZAPRET_DNS_TICK_MS = 3600 * 1000


def install_zapret_dns_server_watch(
    startup_host,
    *,
    dns_feature,
    hosts_feature=None,
    log_startup_metric,
) -> None:
    """Свой сервер Zapret DNS после запуска: адрес по имени и адрес посредника.

    Раз в сутки (шлюз проб, и через перезапуск тоже) программа спрашивает адрес имени
    сервера и адрес его посредника (dns.zapret_dns_server.refresh). Сервер переехал —
    адаптеры тут же переводятся на новый адрес; сменился посредник (или сервер) —
    блок hosts переписывается по каталогу. Задача ходит в сеть и идёт в очереди «dns».
    """

    def _refresh_address() -> None:
        from utils.probe_gate import ZAPRET_DNS_ADDRESS, try_background

        permit = try_background(ZAPRET_DNS_ADDRESS)
        if permit is None:
            return
        with permit:
            change = dns_feature.refresh_zapret_dns_server()
        changed = bool(getattr(change, "changed", False))
        log_startup_metric(
            "ZapretDnsAddressChecked",
            f"changed={int(changed)} ({getattr(change, 'detail', '') or 'без причины'})",
        )
        relay_changed = bool(getattr(change, "relay_changed", False))
        if changed:
            dns_feature.migrate_outdated_dns_addresses()
        if (changed or relay_changed) and hosts_feature is not None:
            enqueue_subsystem_task("hosts", "ZapretDnsHostsRefresh", hosts_feature.refresh_applied_selection)

    def _run() -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            _refresh_address()
        except Exception as exc:
            log(f"Zapret DNS: плановая задача не выполнена: {exc}", "WARNING")

    def _enqueue() -> None:
        if is_startup_host_alive(startup_host):
            enqueue_subsystem_task("dns", "ZapretDnsServerWatch", _run)

    def _on_tick() -> None:
        if not is_startup_host_alive(startup_host):
            return
        _enqueue()
        schedule_after(ZAPRET_DNS_TICK_MS, _on_tick)

    def _start() -> None:
        _enqueue()
        schedule_after(ZAPRET_DNS_TICK_MS, _on_tick)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_zapret_dns_server_watch"]
