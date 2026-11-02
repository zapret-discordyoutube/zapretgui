from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after

# Как часто спрашивать шлюз проб, не пора ли. Сами интервалы решает шлюз:
# utils.probe_gate.ZAPRET_DNS_ADDRESS (сутки) и ZAPRET_DNS_CHECK_IN (два часа).
ZAPRET_DNS_TICK_MS = 3600 * 1000


def install_zapret_dns_server_watch(
    startup_host,
    *,
    dns_feature,
    hosts_feature=None,
    log_startup_metric,
) -> None:
    """Свой сервер Zapret DNS после запуска: адрес по имени и отметка у посредника.

    Раз в сутки (шлюз проб, и через перезапуск тоже) программа спрашивает адрес имени
    сервера (dns.zapret_dns_server.refresh). Сервер переехал — адаптеры тут же
    переводятся на новый адрес, а блок hosts переписывается по каталогу с новым
    адресом. Раз в два часа, пока включён профиль hosts «Zapret DNS», — отметка
    у посредника (check_in). Обе задачи ходят в сеть и идут в очереди «dns».
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
        if not changed:
            return
        dns_feature.migrate_outdated_dns_addresses()
        if hosts_feature is not None:
            enqueue_subsystem_task("hosts", "ZapretDnsHostsRefresh", hosts_feature.refresh_applied_selection)

    def _check_in() -> None:
        from utils.probe_gate import ZAPRET_DNS_CHECK_IN, try_background

        permit = try_background(ZAPRET_DNS_CHECK_IN)
        if permit is None:
            return
        with permit:
            answered = dns_feature.check_in_zapret_dns()
        if answered is not None:
            log_startup_metric("ZapretDnsCheckIn", f"answered={int(bool(answered))}")

    def _run() -> None:
        if not is_startup_host_alive(startup_host):
            return
        for step in (_refresh_address, _check_in):
            try:
                step()
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
