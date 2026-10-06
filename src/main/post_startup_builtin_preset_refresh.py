"""Замена копий встроенных пресетов, отставших по номеру версии.

Пункт 7 договора ``presets.preset_contract``: правка встроенного пресета
сохраняется у пользователя копией, и дальше работает копия. Когда после
обновления программы у встроенного пресета вырос номер ``# BuiltinVersion:``,
копия заменяется им — иначе исправления встроенных пресетов до пользователя не
доходят. Прежняя копия сохраняется в ``presets/replaced``.

Работа идёт после интерактивной готовности окна в подсистемной очереди
"presets" (рабочий поток — диск в GUI-потоке не появляется) и стоит в ней
раньше разового перевода пресетов в обязательный формат: заменённые копии
переводить уже не нужно.
"""

from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task
from settings.mode import ZAPRET1_MODE, ZAPRET2_MODE

_LAUNCH_METHODS = (ZAPRET2_MODE, ZAPRET1_MODE)


def install_builtin_preset_override_refresh(
    startup_host,
    *,
    presets_feature,
    log_startup_metric,
    notify=None,
) -> None:
    def _notify_replaced(file_names: list[str]) -> None:
        """Человек должен увидеть замену: его прежний выбор стратегий в этих пресетах сброшен."""
        if not file_names or not callable(notify):
            return
        try:
            from app_notifications import advisory_notification

            if len(file_names) == 1:
                content = (
                    f"Пресет «{_preset_title(file_names[0])}» заменён новой версией встроенного пресета. "
                    "Ваша прежняя копия сохранена в папке presets\\replaced."
                )
            else:
                content = (
                    f"Пресетов заменено новыми версиями встроенных: {len(file_names)}. "
                    "Ваши прежние копии сохранены в папке presets\\replaced."
                )
            notify(
                advisory_notification(
                    level="info",
                    title="Встроенные пресеты обновлены",
                    content=content,
                    source="presets.builtin_update",
                    presentation="infobar",
                    queue="startup",
                    dedupe_key="presets.builtin_update",
                    dedupe_window_ms=10_000,
                )
            )
        except Exception as exc:
            log(f"Не удалось показать уведомление об обновлении встроенных пресетов: {exc}", "DEBUG")

    def _run_refresh() -> None:
        replaced: list[str] = []
        for launch_method in _LAUNCH_METHODS:
            if not is_startup_host_alive(startup_host):
                return
            try:
                result = presets_feature.refresh_outdated_builtin_overrides(launch_method)
            except Exception as exc:
                log(f"Обновление копий встроенных пресетов ({launch_method}) не выполнено: {exc}", "WARNING")
                continue
            for file_name, old_version, new_version in tuple(getattr(result, "refreshed", ()) or ()):
                replaced.append(str(file_name))
                log(
                    f"Пресет «{file_name}» заменён встроенным версии {new_version} "
                    f"(у вашей копии была {old_version or 'без номера'}); прежняя копия сохранена в presets/replaced",
                    "INFO",
                )
            for file_name, error in tuple(getattr(result, "failed", ()) or ()):
                log(f"Пресет «{file_name}» не удалось заменить встроенным: {error}", "WARNING")
        _notify_replaced(replaced)

    def _enqueue_refresh() -> None:
        if not is_startup_host_alive(startup_host):
            return
        log_startup_metric("StartupBuiltinPresetOverrideRefreshQueued", "all")
        enqueue_subsystem_task("presets", "BuiltinPresetOverrideRefresh", _run_refresh)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _enqueue_refresh,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


def _preset_title(file_name: str) -> str:
    name = str(file_name or "")
    return name[:-4] if name.lower().endswith(".txt") else name


__all__ = ["install_builtin_preset_override_refresh"]
