"""Фоновая автосинхронизация удалённых пресетов после старта приложения.

Периодичность: первый проход через ~25 секунд после интерактивной
готовности окна (позже прогревов, чтобы не толкаться на старте), затем
каждые 6 часов. Сами проверки выполняются в подсистемной очереди
"presets" (рабочий поток) — сеть и диск в GUI-потоке не появляются.
"""

from __future__ import annotations

import time

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after
from settings.mode import ZAPRET1_MODE, ZAPRET2_MODE

REMOTE_PRESETS_SYNC_DELAY_MS = 25_000
REMOTE_PRESETS_SYNC_INTERVAL_MS = 6 * 3600 * 1000
_SYNC_METHODS: tuple[str, ...] = (ZAPRET2_MODE, ZAPRET1_MODE)


def install_remote_presets_sync(
    startup_host,
    *,
    presets_feature,
    log_startup_metric,
    notify=None,
    delay_ms: int = REMOTE_PRESETS_SYNC_DELAY_MS,
    interval_ms: int = REMOTE_PRESETS_SYNC_INTERVAL_MS,
) -> None:
    def _notify_active_preset_updated(method: str, file_name: str) -> None:
        if not callable(notify):
            return
        try:
            selected = str(presets_feature.get_selected_source_preset_file_name(method) or "")
            if selected.strip().casefold() != str(file_name or "").strip().casefold():
                return
            from app_notifications import advisory_notification

            notify(
                advisory_notification(
                    level="success",
                    title="Пресет обновлён из источника",
                    content=f"Активный пресет «{file_name}» обновлён по ссылке, стратегия перезапущена.",
                    source="presets.remote_sync",
                    presentation="infobar",
                    queue="immediate",
                    dedupe_key=f"presets.remote_sync:{file_name.casefold()}",
                    dedupe_window_ms=10_000,
                )
            )
        except Exception as exc:
            log(f"Не удалось показать уведомление об обновлении пресета: {exc}", "DEBUG")

    def _run_remote_presets_sync(method: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        from presets.remote_sync import parse_iso_ts, should_auto_check
        from presets.remote_sync_workers import sync_remote_preset_by_file_name

        try:
            bindings = presets_feature.get_preset_remote_bindings(method)
        except Exception as exc:
            log(f"Автосинк удалённых пресетов {method}: не удалось прочитать привязки: {exc}", "DEBUG")
            return
        now_ts = time.time()
        for file_name, binding in dict(bindings or {}).items():
            if not should_auto_check(binding, now_ts=now_ts, parse_ts=parse_iso_ts):
                continue
            try:
                outcome = sync_remote_preset_by_file_name(presets_feature, method, file_name)
            except Exception as exc:
                log(f"Автосинк пресета '{file_name}' ({method}) упал: {exc}", "ERROR")
                continue
            status = str(getattr(outcome, "status", "") or "")
            detail = str(getattr(outcome, "detail", "") or "")
            if status == "updated":
                log(f"Удалённый пресет '{file_name}' ({method}) обновлён из источника", "INFO")
                _notify_active_preset_updated(method, file_name)
            elif status == "error":
                log(f"Автосинк пресета '{file_name}' ({method}): {detail}", "WARNING")
            elif status == "detached":
                log(
                    f"Пресет '{file_name}' ({method}) изменён локально — автообновление приостановлено",
                    "INFO",
                )

    def _enqueue_sync_round(reason: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        log_startup_metric("RemotePresetsSyncQueued", reason)
        for method in _SYNC_METHODS:
            enqueue_subsystem_task(
                "presets",
                f"RemotePresetsSync-{method}",
                lambda method=method: _run_remote_presets_sync(method),
            )

    def _schedule_periodic_tick() -> None:
        interval = max(60_000, int(interval_ms))
        schedule_after(interval, _on_periodic_tick)

    def _on_periodic_tick() -> None:
        if not is_startup_host_alive(startup_host):
            return
        _enqueue_sync_round("periodic")
        _schedule_periodic_tick()

    def _schedule_remote_presets_sync() -> None:
        if not is_startup_host_alive(startup_host):
            return
        delay = max(0, int(delay_ms))
        log(f"Автосинк удалённых пресетов отложен на {delay}ms", "DEBUG")
        schedule_after(
            delay,
            lambda: is_startup_host_alive(startup_host) and _enqueue_sync_round("startup"),
        )
        _schedule_periodic_tick()

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _schedule_remote_presets_sync,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = [
    "REMOTE_PRESETS_SYNC_DELAY_MS",
    "REMOTE_PRESETS_SYNC_INTERVAL_MS",
    "install_remote_presets_sync",
]
