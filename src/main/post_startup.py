from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def install_startup_checks(*args, **kwargs):
    from main.post_startup_checks import install_startup_checks as install

    return install(*args, **kwargs)


def install_backend_page_data_warmup(*args, **kwargs):
    from main.post_startup_backend_warmup import install_backend_page_data_warmup as install

    return install(*args, **kwargs)


def install_cpu_diagnostic(*args, **kwargs):
    from main.post_startup_diagnostics import install_cpu_diagnostic as install

    return install(*args, **kwargs)


def install_idle_memory_trim(startup_host) -> None:
    startup_host.install_idle_memory_trim()


def install_global_exception_handler(*args, **kwargs):
    from main.post_startup_diagnostics import install_global_exception_handler as install

    return install(*args, **kwargs)


def install_qt_event_diagnostic_probe(*args, **kwargs):
    from main.post_startup_diagnostics import install_qt_event_diagnostic_probe as install

    return install(*args, **kwargs)


def install_startup_audit(*args, **kwargs):
    from main.startup_audit import install_startup_audit as install

    return install(*args, **kwargs)


def install_dns_address_migration(*args, **kwargs):
    from main.post_startup_dns_migration import install_dns_address_migration as install

    return install(*args, **kwargs)


def install_hosts_applied_selection_refresh(*args, **kwargs):
    from main.post_startup_hosts_refresh import install_hosts_applied_selection_refresh as install

    return install(*args, **kwargs)


def install_dns_page_data_warmup(*args, **kwargs):
    from main.post_startup_dns_warmup import install_dns_page_data_warmup as install

    return install(*args, **kwargs)


def install_hosts_page_warmup(*args, **kwargs):
    from main.post_startup_hosts_warmup import install_hosts_page_warmup as install

    return install(*args, **kwargs)


def install_lists_check(*args, **kwargs):
    from main.post_startup_lists import install_lists_check as install

    return install(*args, **kwargs)


def install_deferred_maintenance(*args, **kwargs):
    from main.post_startup_maintenance import install_deferred_maintenance as install

    return install(*args, **kwargs)


def install_profile_warmup(*args, **kwargs):
    from main.post_startup_profile_warmup import install_profile_warmup as install

    return install(*args, **kwargs)


def install_user_presets_warmup(*args, **kwargs):
    from main.post_startup_user_presets_warmup import install_user_presets_warmup as install

    return install(*args, **kwargs)


def install_builtin_preset_override_refresh(*args, **kwargs):
    from main.post_startup_builtin_preset_refresh import install_builtin_preset_override_refresh as install

    return install(*args, **kwargs)


def install_user_preset_contract_migration(*args, **kwargs):
    from main.post_startup_preset_contract_migration import install_user_preset_contract_migration as install

    return install(*args, **kwargs)


def install_remote_presets_sync(*args, **kwargs):
    from main.post_startup_remote_presets import install_remote_presets_sync as install

    return install(*args, **kwargs)


def install_onboarding_tour(*args, **kwargs):
    from main.post_startup_onboarding import install_onboarding_tour as install

    return install(*args, **kwargs)


def install_telegram_proxy_startup(*args, **kwargs):
    from main.post_startup_proxy import install_telegram_proxy_startup as install

    return install(*args, **kwargs)


def install_telegram_proxy_page_warmup(*args, **kwargs):
    from main.post_startup_telegram_proxy_warmup import install_telegram_proxy_page_warmup as install

    return install(*args, **kwargs)


def install_after_interactive_import_warmup(*args, **kwargs):
    from main.post_startup_import_warmup import install_after_interactive_import_warmup as install

    return install(*args, **kwargs)


def install_secondary_page_warmup(*args, **kwargs):
    from main.post_startup_secondary_page_warmup import install_secondary_page_warmup as install

    return install(*args, **kwargs)


def build_idle_ui_task_queue(startup_host, **kwargs):
    from main.post_startup_idle_tasks import build_idle_ui_task_queue as build

    return build(startup_host, **kwargs)


# Обход запускается или останавливается: рабочий поток запуска занят, а на
# главной странице идёт анимация пуска.
_LAUNCH_TRANSITION_PHASES = frozenset({"autostart_pending", "starting", "stopping"})


def _launch_transition_probe(ui_state_store):
    if ui_state_store is None:
        return None

    def _is_launch_busy() -> bool:
        phase = str(ui_state_store.snapshot().launch_phase or "").strip().lower()
        return phase in _LAUNCH_TRANSITION_PHASES

    return _is_launch_busy


def install_update_check(*args, **kwargs):
    from main.post_startup_update import install_update_check as install

    return install(*args, **kwargs)


def install_installation_integrity_check(*args, **kwargs):
    from main.post_startup_integrity import install_installation_integrity_check as install

    return install(*args, **kwargs)

@dataclass(frozen=True, slots=True)
class PostStartupDeps:
    startup_host: Any
    profile_feature: Any
    dns_feature: Any
    notify: Any
    notify_many: Any
    set_status: Any
    log_startup_metric: Any
    start_proxy_if_enabled_async: Any
    startup_lists_check: Any
    install_tray_post_startup: Any
    updater_feature: Any
    request_installation_repair: Any = None
    hosts_feature: Any = None
    premium_feature: Any = None
    logs_feature: Any = None
    presets_feature: Any = None
    ui_state_store: Any = None
    launch_method: str = ""


def install_post_startup_tasks(deps: PostStartupDeps) -> None:
    startup_host = deps.startup_host
    # Сборка скрытых страниц занимает GUI-поток, поэтому идёт через общую
    # очередь: по одной странице, только в паузах пользователя и только пока
    # молчит фон — дорожка фоновых задач пуста и обход не запускается.
    idle_tasks = build_idle_ui_task_queue(
        startup_host,
        is_launch_busy=_launch_transition_probe(getattr(deps, "ui_state_store", None)),
    )
    on_profile_warmup_ready = None
    if deps.presets_feature is not None and deps.ui_state_store is not None:
        on_profile_warmup_ready = lambda method: deps.presets_feature.refresh_profile_strategy_summary_in_store(
            method=method,
            profile_feature=deps.profile_feature,
            ui_state_store=deps.ui_state_store,
        )

    install_startup_checks(
        startup_host,
        notify_many=deps.notify_many,
        set_status=deps.set_status,
        log_startup_metric=deps.log_startup_metric,
    )
    install_deferred_maintenance(
        startup_host,
        notify_many=deps.notify_many,
        log_startup_metric=deps.log_startup_metric,
    )
    install_telegram_proxy_startup(
        startup_host,
        start_proxy_if_enabled_async=deps.start_proxy_if_enabled_async,
        log_startup_metric=deps.log_startup_metric,
    )
    install_after_interactive_import_warmup(
        startup_host,
        log_startup_metric=deps.log_startup_metric,
    )
    install_telegram_proxy_page_warmup(
        startup_host,
        log_startup_metric=deps.log_startup_metric,
        idle_tasks=idle_tasks,
    )
    install_secondary_page_warmup(
        startup_host,
        log_startup_metric=deps.log_startup_metric,
        idle_tasks=idle_tasks,
    )
    install_lists_check(
        startup_host,
        startup_lists_check=deps.startup_lists_check,
        log_startup_metric=deps.log_startup_metric,
    )
    install_dns_address_migration(
        startup_host,
        dns_feature=deps.dns_feature,
        log_startup_metric=deps.log_startup_metric,
    )
    install_dns_page_data_warmup(
        startup_host,
        dns_feature=deps.dns_feature,
        log_startup_metric=deps.log_startup_metric,
    )
    install_hosts_applied_selection_refresh(
        startup_host,
        hosts_feature=deps.hosts_feature,
        log_startup_metric=deps.log_startup_metric,
    )
    install_hosts_page_warmup(
        startup_host,
        hosts_feature=deps.hosts_feature,
        log_startup_metric=deps.log_startup_metric,
    )
    if deps.premium_feature is not None and deps.logs_feature is not None:
        install_backend_page_data_warmup(
            startup_host,
            premium_feature=deps.premium_feature,
            logs_feature=deps.logs_feature,
            log_startup_metric=deps.log_startup_metric,
        )
    install_profile_warmup(
        startup_host,
        profile_feature=deps.profile_feature,
        log_startup_metric=deps.log_startup_metric,
        idle_tasks=idle_tasks,
        current_launch_method=str(getattr(deps, "launch_method", "") or ""),
        on_profile_warmup_ready=on_profile_warmup_ready,
    )
    if deps.presets_feature is not None:
        # Раньше перевода в обязательный формат: заменённую копию переводить не нужно.
        # В 21.1.7.19 эта задача подвесила окно: её уведомление создавалось в
        # фоновом потоке. Сама замена была ни при чём; уведомления теперь
        # безопасны из любого потока (WindowNotificationCenter.notify).
        install_builtin_preset_override_refresh(
            startup_host,
            presets_feature=deps.presets_feature,
            log_startup_metric=deps.log_startup_metric,
            notify=deps.notify,
        )
        install_user_preset_contract_migration(
            startup_host,
            presets_feature=deps.presets_feature,
            log_startup_metric=deps.log_startup_metric,
        )
        install_user_presets_warmup(
            startup_host,
            presets_feature=deps.presets_feature,
            log_startup_metric=deps.log_startup_metric,
            current_launch_method=str(getattr(deps, "launch_method", "") or ""),
        )
        install_remote_presets_sync(
            startup_host,
            presets_feature=deps.presets_feature,
            log_startup_metric=deps.log_startup_metric,
            notify=deps.notify,
        )
    deps.install_tray_post_startup()
    install_update_check(
        startup_host,
        updater_feature=deps.updater_feature,
        notify=deps.notify,
        set_status=deps.set_status,
        idle_tasks=idle_tasks,
        ui_state_store=getattr(deps, "ui_state_store", None),
    )
    request_installation_repair = getattr(deps, "request_installation_repair", None)
    if callable(request_installation_repair):
        install_installation_integrity_check(
            startup_host,
            updater_feature=deps.updater_feature,
            request_repair=request_installation_repair,
            log_startup_metric=deps.log_startup_metric,
        )
    install_onboarding_tour(
        startup_host,
        log_startup_metric=deps.log_startup_metric,
    )
    install_idle_memory_trim(startup_host)
    install_cpu_diagnostic()
    install_qt_event_diagnostic_probe()
    install_startup_audit()
    install_global_exception_handler()


__all__ = ["PostStartupDeps", "install_post_startup_tasks"]
