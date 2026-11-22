from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after

# Как часто спрашивать шлюз проб, не пора ли проверить адреса. Сам интервал проверки
# (раз в шесть часов, и через перезапуск тоже; оборванную — через полчаса) решает шлюз: utils.probe_gate.HOSTS_ADDRESSES.
HOSTS_ADDRESS_CHECK_TICK_MS = 3600 * 1000


def install_hosts_applied_selection_refresh(
    startup_host,
    *,
    hosts_feature=None,
    log_startup_metric,
) -> None:
    """После запуска сверяет блок ZapretGUI в hosts и плановым порядком проверяет адреса.

    Два шага. Сначала умный движок проверяет адреса включённых сервисов — если
    шлюз проб разрешает (``utils.probe_gate``: не чаще раза в шесть часов, даже
    через перезапуск; не во время проверки по кнопке). Плановая проверка трогает
    только первые адреса, поиск замены — лишь при беде. Это ожидание сети, поэтому
    оно идёт в своей очереди и остальным задачам запуска не мешает. Затем блок в
    hosts переписывается, если проверенные адреса или новый каталог отличаются от
    записанного: пользователю не нужно заново нажимать «применить».
    """

    def _run_hosts_applied_selection_refresh() -> None:
        if not is_startup_host_alive(startup_host) or hosts_feature is None:
            return
        try:
            result = hosts_feature.refresh_applied_selection()
        except Exception as exc:
            log(f"Обновление адресов hosts при запуске не выполнено: {exc}", "WARNING")
            return
        changed = bool(getattr(result, "changed", False))
        # Причина нужна в журнале поддержки: по «changed=False» не понять, почему старые адреса остались.
        reason = str(getattr(result, "message", "") or "причина не названа")
        log_startup_metric("StartupHostsAppliedSelectionRefreshFinished", f"changed={changed} ({reason})")

    def _enqueue_refresh() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task(
            "hosts",
            "HostsAppliedSelectionRefresh",
            _run_hosts_applied_selection_refresh,
        )

    def _run_hosts_address_check() -> None:
        from utils.probe_gate import HOSTS_ADDRESSES, Why, try_background

        try:
            if is_startup_host_alive(startup_host) and hosts_feature is not None:
                permit = try_background(HOSTS_ADDRESSES)
                if permit is None:
                    log_startup_metric("StartupHostsAddressCheckSkipped", "шлюз проб: ещё рано или идёт проверка по кнопке")
                    return
                with permit:
                    report = hosts_feature.check_addresses(why=Why.SCHEDULE)
                    # «Сделано» — только если проверка дошла до конца и сеть была: без сети она ничего не проверила
                    # (EngineReport.network), и повторить её нужно через полчаса, а не через шесть часов.
                    if getattr(report, "network", True):
                        permit.complete()
                log_startup_metric(
                    "StartupHostsAddressCheckFinished",
                    f"checked={getattr(report, 'checked', 0)} changed={bool(getattr(report, 'changed', False))}",
                )
        except Exception as exc:
            log(f"Проверка адресов hosts не выполнена: {exc}", "WARNING")
        finally:
            # Запись в hosts — на общей дорожке: она не должна идти одновременно с другими задачами диска.
            _enqueue_refresh()

    def _start_hosts_applied_selection_refresh() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task("dns", "HostsAddressCheck", _run_hosts_address_check)

    def _on_periodic_tick() -> None:
        if not is_startup_host_alive(startup_host):
            return
        _start_hosts_applied_selection_refresh()
        schedule_after(HOSTS_ADDRESS_CHECK_TICK_MS, _on_periodic_tick)

    def _start_and_schedule() -> None:
        _start_hosts_applied_selection_refresh()
        schedule_after(HOSTS_ADDRESS_CHECK_TICK_MS, _on_periodic_tick)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start_and_schedule,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_hosts_applied_selection_refresh"]
