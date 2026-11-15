from __future__ import annotations

from typing import Callable

from platforms import current as current_platform
from platforms.base import GuiAutostartResult


def _gui_autostart_executable() -> str:
    """Возвращает единственную допустимую точку запуска установленного GUI."""
    from config.runtime_layout import APPLICATION_PATHS

    return str(APPLICATION_PATHS.executable)


def get_current_launch_method() -> str:
    from settings.dpi.public import get_launch_method

    return str(get_launch_method() or "").strip()


def save_gui_autostart_enabled(enabled: bool) -> bool:
    from settings.store import set_gui_autostart_enabled

    return bool(set_gui_autostart_enabled(bool(enabled)))


def disable_gui_autostart() -> GuiAutostartResult:
    return current_platform().autostart.disable()


def enable_gui_autostart(*, status_cb: Callable[[str], None] | None = None) -> GuiAutostartResult:
    return current_platform().autostart.enable(_gui_autostart_executable(), status_cb=status_cb)


def ensure_gui_autostart_migrated() -> bool:
    """Чинит автозапуск, если он включён настройкой, но записан по-старому или указывает не туда.

    Как именно записан автозапуск (Windows: задача планировщика, Linux: запись XDG), решает платформа.
    """
    try:
        from settings.store import get_gui_autostart_enabled

        if not get_gui_autostart_enabled():
            return False
        return bool(current_platform().autostart.ensure_current(_gui_autostart_executable()))
    except Exception as exc:
        try:
            from log.log import log

            log(f"GUI autostart migration failed: {exc}", "WARNING")
        except Exception:
            pass
        return False


__all__ = [
    "GuiAutostartResult",
    "disable_gui_autostart",
    "enable_gui_autostart",
    "ensure_gui_autostart_migrated",
    "get_current_launch_method",
    "save_gui_autostart_enabled",
]
