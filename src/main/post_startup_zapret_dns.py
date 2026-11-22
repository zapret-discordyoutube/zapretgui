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
    """Свой сервер Zapret DNS после запуска: адрес по имени и адрес посредника; адреса чужих серверов.

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

    def _refresh_provider_addresses() -> None:
        """Чужие серверы с адресами по имени DoH: своё разрешение шлюза и своя отметка «сделано».

        Проверка идёт около минуты. Своё разрешение (а не общее с быстрой проверкой Zapret DNS) нужно,
        чтобы быстрая проверка не расходовала сутки долгой; а «сделано» пишется только когда проверка
        дошла до конца: запуск, оборванный на середине, повторит её через полчаса, а не через сутки.
        """
        from utils.probe_gate import PROVIDER_ADDRESSES, try_background

        permit = try_background(PROVIDER_ADDRESSES)
        if permit is None:
            return
        with permit:
            try:
                provider_changes = list(dns_feature.refresh_provider_addresses() or [])
            except Exception as exc:
                log(f"Адреса серверов DNS по имени не узнаны: {exc}", "WARNING")
                return
            completed = bool(dns_feature.provider_run_completed(provider_changes))
            if completed:
                permit.complete()
        changed_names = [getattr(item, "name", "") for item in provider_changes if getattr(item, "changed", False)]
        log_startup_metric(
            "ProviderDnsAddressesChecked",
            f"servers={len(provider_changes)} changed={len(changed_names)} completed={int(completed)}",
        )
        if changed_names:
            dns_feature.migrate_outdated_dns_addresses()

    def _run() -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            _refresh_address()
        except Exception as exc:
            log(f"Zapret DNS: плановая задача не выполнена: {exc}", "WARNING")
        # Вторая проверка независима от первой: сбой одной не отменяет другую.
        try:
            _refresh_provider_addresses()
        except Exception as exc:
            log(f"Адреса чужих серверов DNS: плановая задача не выполнена: {exc}", "WARNING")

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
