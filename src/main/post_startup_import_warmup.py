from __future__ import annotations

from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after


# Нужны только фоновым задачам после старта (подписка, обновления,
# Telegram Proxy), а не сборке окна. Пока окно строится, фоновый импорт
# отбирал бы у главного потока GIL, поэтому греем их после готовности
# интерфейса. asyncio: первое обращение к Telegram Proxy тянет его вместе с
# asyncio.windows_events, и без прогрева это давало рывок на ~64 мс.
#
# Модули трея и Telegram Proxy нужны значку в трее: он создаётся в GUI-потоке
# через ~2 секунды после запуска, и первый импорт этих модулей прямо там
# задерживал кадр на ~65 мс. В списке только модули без объектов Qt на уровне
# файла: объявлять классы в фоновом потоке можно, создавать QObject — нет.
AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES = (
    "asyncio",
    "requests",
    "psutil",
    "telegram_proxy.runtime.commands",
    "telegram_proxy.manager",
    "tray",
)
# Первую секунду после появления окна интерфейс занят сам: страница
# «всплывает». Фоновый импорт в это время делил бы с GUI-потоком GIL, поэтому
# начинается позже — но раньше значка в трее, которому эти модули нужны.
# Идёт по общей дорожке фоновых задач запуска: импорт — чистая работа
# процессора и диска.
AFTER_INTERACTIVE_IMPORT_WARMUP_DELAY_MS = 1_000


def install_after_interactive_import_warmup(startup_host, *, log_startup_metric) -> None:
    def _run_warmup() -> None:
        from main.entry import warm_up_modules

        warmed = warm_up_modules(AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES)
        log_startup_metric("StartupImportWarmupFinished", ", ".join(warmed))

    def _start_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task("imports", "import-warmup-after-interactive", _run_warmup)

    def _schedule_warmup() -> None:
        if not is_startup_host_alive(startup_host):
            return
        schedule_after(AFTER_INTERACTIVE_IMPORT_WARMUP_DELAY_MS, _start_warmup)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _schedule_warmup,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = [
    "AFTER_INTERACTIVE_IMPORT_WARMUP_DELAY_MS",
    "AFTER_INTERACTIVE_IMPORT_WARMUP_MODULES",
    "install_after_interactive_import_warmup",
]
