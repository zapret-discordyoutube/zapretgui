from __future__ import annotations

from log.log import log
from settings import store as settings_store
from settings.mode import (
    DEFAULT_LAUNCH_METHOD,
    is_known_launch_method,
    normalize_launch_method,
)

def get_strategy_launch_method() -> str:
    try:
        return normalize_launch_method(settings_store.get_strategy_launch_method())
    except Exception as e:
        log(f"Ошибка чтения метода запуска из settings.sqlite3: {e}", "ERROR")
    return DEFAULT_LAUNCH_METHOD


def set_strategy_launch_method(method: str) -> bool:
    try:
        normalized = normalize_launch_method(method, default="")
        if not is_known_launch_method(normalized):
            log(
                f"Попытка сохранить неподдерживаемый метод запуска: {normalized or 'empty'}. "
                f"Используем {DEFAULT_LAUNCH_METHOD}",
                "WARNING",
            )
            normalized = DEFAULT_LAUNCH_METHOD
        settings_store.set_strategy_launch_method(normalized)
        log(f"Метод запуска стратегий изменен на: {normalized}", "INFO")
        return True
    except Exception as e:
        log(f"Ошибка сохранения метода запуска: {e}", "ERROR")
        return False


def get_wssize_enabled(*, launch_method: str | None = None) -> bool:
    _ = launch_method
    return False


def set_wssize_enabled(
    enabled: bool,
    *,
    launch_method: str | None = None,
    runtime_reload_callback=None,
) -> bool:
    _ = enabled, launch_method, runtime_reload_callback
    return False


def get_debug_log_enabled(*, launch_method: str | None = None) -> bool:
    _ = launch_method
    return False


def set_debug_log_enabled(
    enabled: bool,
    *,
    launch_method: str | None = None,
    runtime_reload_callback=None,
) -> bool:
    _ = enabled, launch_method, runtime_reload_callback
    return False


def get_debug_log_file(*, launch_method: str | None = None) -> str:
    _ = launch_method
    return ""


__all__ = [
    "get_strategy_launch_method",
    "set_strategy_launch_method",
    "get_wssize_enabled",
    "set_wssize_enabled",
    "get_debug_log_enabled",
    "get_debug_log_file",
    "set_debug_log_enabled",
]
