from __future__ import annotations

from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import start_daemon_thread


# Нужны только фоновым задачам после старта (подписка, обновления,
# Telegram Proxy), а не сборке окна. Пока окно строится, фоновый импорт
# отбирал бы у главного потока GIL, поэтому греем их после готовности
# интерфейса. asyncio: первое обращение к Telegram Proxy тянет его вместе с
# asyncio.windows_events, и без прогрева это давало рывок на ~64 мс.
AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES = ("asyncio", "requests", "psutil")


def install_after_interactive_import_warmup(startup_host, *, log_startup_metric) -> None:
    def _run_warmup() -> None:
        from main.entry import warm_up_modules

        warmed = warm_up_modules(AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES)
        log_startup_metric("StartupImportWarmupFinished", ", ".join(warmed))

    def _start_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        start_daemon_thread("import-warmup-after-interactive", _run_warmup)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start_warmup,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES", "install_after_interactive_import_warmup"]
