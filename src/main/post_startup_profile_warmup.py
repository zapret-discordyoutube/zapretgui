from __future__ import annotations

import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after
from settings.mode import ZAPRET2_MODE, is_preset_launch_method, normalize_launch_method
from ui.navigation_pages import resolve_preset_setup_page_for_method, resolve_profile_setup_page_for_method
from ui.performance_metrics import log_ui_timing_since


DEFAULT_PROFILE_WARMUP_METHOD = ZAPRET2_MODE
PROFILE_WARMUP_DELAY_MS = 0
PRESET_SETUP_PAGE_WARMUP_DELAY_MS = 2_200
PROFILE_SETUP_PAGE_WARMUP_DELAY_MS = 1_000


class _ProfileWarmupBridge(QObject):
    method_ready = pyqtSignal(str)


def profile_warmup_method(current_method: str) -> str:
    """Греем только активный метод: неактивный прогреется при переключении режима."""
    current = normalize_launch_method(current_method)
    if not is_preset_launch_method(current):
        return DEFAULT_PROFILE_WARMUP_METHOD
    return current


def install_profile_warmup(
    startup_host,
    *,
    profile_feature,
    log_startup_metric,
    current_launch_method: str = DEFAULT_PROFILE_WARMUP_METHOD,
    delay_ms: int = PROFILE_WARMUP_DELAY_MS,
    preset_setup_page_delay_ms: int = PRESET_SETUP_PAGE_WARMUP_DELAY_MS,
    profile_setup_page_delay_ms: int = PROFILE_SETUP_PAGE_WARMUP_DELAY_MS,
    on_profile_warmup_ready: Callable[[str], None] | None = None,
) -> None:
    warmup_bridge = _ProfileWarmupBridge()
    if on_profile_warmup_ready is not None:
        warmup_bridge.method_ready.connect(on_profile_warmup_ready)

    def _run_profile_warmup_method(method: str) -> None:
        if not is_startup_host_alive(startup_host):
            return
        started_at = time.perf_counter()
        try:
            profile_feature.warm_profile_list(method)
        except Exception as exc:
            log(f"Фоновый прогрев профилей {method} не выполнен: {exc}", "DEBUG")
            return
        log_ui_timing_since("warmup", method, "profile.full", started_at, important=True)
        if not is_startup_host_alive(startup_host):
            return
        if on_profile_warmup_ready is not None:
            try:
                warmup_bridge.method_ready.emit(method)
            except Exception as exc:
                log(f"Не удалось обновить UI после прогрева профилей {method}: {exc}", "DEBUG")

    def _run_preset_setup_page_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        page_name = resolve_preset_setup_page_for_method(current_launch_method)
        if page_name is None:
            return
        started_at = time.perf_counter()
        try:
            page = startup_host.ensure_page(page_name)
            if page is None:
                return
            warmup_initial_load = getattr(page, "warmup_initial_load", None)
            if callable(warmup_initial_load):
                warmup_initial_load()
            log_startup_metric("StartupPresetSetupUiWarmupFinished", page_name.name)
        except Exception as exc:
            log(f"Фоновая подготовка страницы профилей preset-а не выполнена: {exc}", "DEBUG")
            return
        log_ui_timing_since("warmup", page_name, "ui_page.preset_setup", started_at, important=True)

    def _run_profile_setup_page_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        page_name = resolve_profile_setup_page_for_method(current_launch_method)
        if page_name is None:
            return
        started_at = time.perf_counter()
        try:
            page = startup_host.ensure_page(page_name)
            if page is None:
                return
            log_startup_metric("StartupProfileSetupUiWarmupFinished", page_name.name)
        except Exception as exc:
            log(f"Фоновая подготовка страницы настройки profile-а не выполнена: {exc}", "DEBUG")
            return
        log_ui_timing_since("warmup", page_name, "ui_page.profile_setup", started_at, important=True)

    def _start_profile_warmup(method: str) -> None:
        log_startup_metric("StartupProfileWarmupStarted", method)
        enqueue_subsystem_task(
            "profile",
            f"ProfileWarmup-{method}",
            lambda: _run_profile_warmup_method(method),
        )

    def _schedule_profile_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        delay = max(0, int(delay_ms))
        method = profile_warmup_method(current_launch_method)
        log_startup_metric("StartupProfileWarmupQueued", f"{delay}ms current after interactive")
        log(f"Фоновый прогрев профилей отложен на {delay}ms", "DEBUG")
        schedule_after(
            delay,
            lambda: is_startup_host_alive(startup_host) and _start_profile_warmup(method),
        )
        profile_page_delay = max(delay, int(profile_setup_page_delay_ms))
        log_startup_metric("StartupProfileSetupUiWarmupQueued", f"{profile_page_delay}ms after interactive")
        schedule_after(
            profile_page_delay,
            lambda: is_startup_host_alive(startup_host) and _run_profile_setup_page_warmup(),
        )
        page_delay = max(delay, int(preset_setup_page_delay_ms))
        log_startup_metric("StartupPresetSetupUiWarmupQueued", f"{page_delay}ms after interactive")
        schedule_after(
            page_delay,
            lambda: is_startup_host_alive(startup_host) and _run_preset_setup_page_warmup(),
        )

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _schedule_profile_warmup,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = [
    "DEFAULT_PROFILE_WARMUP_METHOD",
    "PRESET_SETUP_PAGE_WARMUP_DELAY_MS",
    "PROFILE_SETUP_PAGE_WARMUP_DELAY_MS",
    "PROFILE_WARMUP_DELAY_MS",
    "install_profile_warmup",
    "profile_warmup_method",
]
