from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task


def install_hosts_applied_selection_refresh(
    startup_host,
    *,
    hosts_feature=None,
    log_startup_metric,
) -> None:
    """Сразу после запуска обновляет адреса в уже записанном блоке ZapretGUI в hosts.

    Если в новой версии каталога у выбранных DNS-профилей сменились адреса,
    пользователю не нужно заново нажимать «применить»: блок переписывается сам.
    Задача ставится в очередь «hosts» раньше прогрева страницы Hosts.
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

    def _start_hosts_applied_selection_refresh() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task(
            "hosts",
            "HostsAppliedSelectionRefresh",
            _run_hosts_applied_selection_refresh,
        )

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start_hosts_applied_selection_refresh,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_hosts_applied_selection_refresh"]
