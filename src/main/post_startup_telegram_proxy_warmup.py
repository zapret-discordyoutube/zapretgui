from __future__ import annotations

import time

from app.page_names import PageName
from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from ui.performance_metrics import log_ui_timing_since


TELEGRAM_PROXY_PAGE_WARMUP_DELAY_MS = 3_000


def install_telegram_proxy_page_warmup(
    startup_host,
    *,
    log_startup_metric,
    idle_tasks,
    delay_ms: int = TELEGRAM_PROXY_PAGE_WARMUP_DELAY_MS,
) -> None:
    def _run_telegram_proxy_page_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        started_at = time.perf_counter()
        try:
            page = startup_host.ensure_page(PageName.TELEGRAM_PROXY)
            if page is None:
                return
            log_startup_metric("StartupTelegramProxyPageWarmupFinished", "ui_page")
        except Exception as exc:
            log(f"Фоновая подготовка страницы Telegram Proxy не выполнена: {exc}", "DEBUG")
            return
        log_ui_timing_since("warmup", PageName.TELEGRAM_PROXY, "ui_page.telegram_proxy", started_at, important=True)

    def _schedule_telegram_proxy_page_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        delay = max(0, int(delay_ms))
        log_startup_metric("StartupTelegramProxyPageWarmupQueued", f"{delay}ms after interactive")
        idle_tasks.add(
            "TelegramProxyPageWarmup",
            _run_telegram_proxy_page_warmup,
            delay_ms=delay,
        )

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _schedule_telegram_proxy_page_warmup,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["TELEGRAM_PROXY_PAGE_WARMUP_DELAY_MS", "install_telegram_proxy_page_warmup"]
