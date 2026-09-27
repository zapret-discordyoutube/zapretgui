"""Разовый перевод пресетов пользователя в обязательный формат winws2.

Пункт 6 договора ``presets.preset_contract``: пресеты winws2 из папки
пользователя, сохранённые старыми версиями, могут не содержать полный
обязательный блок ``--lua-init``. После интерактивной готовности окна каждый
такой файл один раз проходит нормализацию сохранения в подсистемной очереди
"presets" (рабочий поток — диск в GUI-потоке не появляется). Флаг «уже сделано»
не нужен: повторный проход ничего не находит.
"""

from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task
from settings.mode import ZAPRET2_MODE


def install_user_preset_contract_migration(
    startup_host,
    *,
    presets_feature,
    log_startup_metric,
) -> None:
    def _run_migration() -> None:
        if not is_startup_host_alive(startup_host):
            return
        try:
            result = presets_feature.migrate_user_presets_to_save_contract(ZAPRET2_MODE)
        except Exception as exc:
            log(f"Перевод пресетов winws2 в обязательный формат не выполнен: {exc}", "WARNING")
            return
        for file_name in tuple(getattr(result, "migrated", ()) or ()):
            log(
                f"Пресет winws2 «{file_name}» приведён к обязательному формату (полный блок --lua-init)",
                "INFO",
            )
        for file_name, error in tuple(getattr(result, "failed", ()) or ()):
            log(f"Пресет winws2 «{file_name}» пропущен при переводе в обязательный формат: {error}", "WARNING")

    def _enqueue_migration() -> None:
        if not is_startup_host_alive(startup_host):
            return
        log_startup_metric("StartupUserPresetContractMigrationQueued", ZAPRET2_MODE)
        enqueue_subsystem_task("presets", "UserPresetContractMigration", _run_migration)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _enqueue_migration,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_user_preset_contract_migration"]
