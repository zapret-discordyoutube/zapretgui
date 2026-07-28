"""Фоновая подготовка страниц, которые пользователь открывает первым кликом.

Первый показ страницы строит её виджеты и добавляет их в стек прямо в
GUI-потоке. В логе это давало рывки на 64–70 мс ровно в момент клика по
сайдбару (`show_page → ensure_page → stacked_widget.addWidget`). Прогрев
переносит ту же работу в паузу после старта, когда пользователь ничего не
делает и рывок незаметен.

Страницы греются по одной с разносом по времени: одновременная сборка
нескольких страниц снова заняла бы GUI-поток одним куском.
"""

from __future__ import annotations

import time

from app.page_names import PageName
from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import schedule_after
from ui.performance_metrics import log_ui_timing_since


# Порядок и задержки подобраны так, чтобы не пересекаться с уже имеющимися
# прогревами (профиль — 1000 мс, пресеты — 2200 мс, Telegram Proxy — 3000 мс).
SECONDARY_PAGE_WARMUP_PLAN: tuple[tuple[PageName, int], ...] = (
    (PageName.ZAPRET2_USER_PRESETS, 5_000),
    (PageName.APPEARANCE, 6_200),
    (PageName.PREMIUM, 7_400),
)


def install_secondary_page_warmup(
    startup_host,
    *,
    log_startup_metric,
    plan: tuple[tuple[PageName, int], ...] = SECONDARY_PAGE_WARMUP_PLAN,
) -> None:
    def _warm_page(page_name: PageName) -> None:
        if not is_startup_host_alive(startup_host):
            return
        started_at = time.perf_counter()
        try:
            page = startup_host.ensure_page(page_name)
        except Exception as exc:
            log(f"Фоновая подготовка страницы {page_name.name} не выполнена: {exc}", "DEBUG")
            return
        if page is None:
            return
        log_startup_metric("StartupSecondaryPageWarmupFinished", page_name.name)
        log_ui_timing_since(
            "warmup",
            page_name,
            f"ui_page.secondary.{page_name.name.lower()}",
            started_at,
            important=True,
        )

    def _schedule_secondary_page_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        for page_name, delay_ms in plan:
            delay = max(0, int(delay_ms))
            log_startup_metric(
                "StartupSecondaryPageWarmupQueued",
                f"{page_name.name} {delay}ms after interactive",
            )
            schedule_after(
                delay,
                lambda name=page_name: is_startup_host_alive(startup_host) and _warm_page(name),
            )

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _schedule_secondary_page_warmup,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["SECONDARY_PAGE_WARMUP_PLAN", "install_secondary_page_warmup"]
