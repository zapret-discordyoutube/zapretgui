from __future__ import annotations

import ntpath
from typing import Callable

from platforms.base import GuiAutostartResult


def _same_windows_path(left: str, right: str) -> bool:
    def _normalize(value: str) -> str:
        return ntpath.normcase(ntpath.normpath(str(value or "").strip().strip('"')))

    return bool(left and right) and _normalize(left) == _normalize(right)


class WindowsGuiAutostart:
    """Scheduler task at logon; the old Startup-folder shortcut is only found and removed.

    The helper modules are imported inside the methods: they pull in ``ctypes``/``subprocess`` tables
    that nobody needs until the person touches the autostart switch.
    """

    def disable(self) -> GuiAutostartResult:
        from platforms.windows.scheduled_task import autostart_task_exists, delete_autostart_task
        from platforms.windows.startup_shortcut import delete_startup_shortcut, get_startup_shortcut_path

        removed_count = 0
        if delete_autostart_task():
            removed_count += 1
        if delete_startup_shortcut():
            removed_count += 1
        # "Could not delete" and "there was nothing to delete" look the same to the deletion:
        # check that the task and the shortcut are really gone. Otherwise the setting would be saved
        # as "off" while the program kept starting with Windows.
        leftovers: list[str] = []
        if autostart_task_exists():
            leftovers.append("задачу планировщика")
        if get_startup_shortcut_path().exists():
            leftovers.append("ярлык автозагрузки")
        if leftovers:
            return GuiAutostartResult(
                success=False,
                removed_count=removed_count,
                message=f"Windows не дала удалить {' и '.join(leftovers)}. Попробуйте ещё раз.",
            )
        return GuiAutostartResult(success=True, removed_count=removed_count)

    def enable(self, executable: str, *, status_cb: Callable[[str], None] | None = None) -> GuiAutostartResult:
        from platforms.windows.scheduled_task import create_or_update_autostart_task
        from platforms.windows.startup_shortcut import delete_startup_shortcut

        def _status(message: str) -> None:
            if status_cb is not None:
                status_cb(message)

        try:
            _status("Удаление старого ярлыка автозапуска...")
            delete_startup_shortcut()
            _status("Создание задачи автозапуска в Планировщике Windows...")
            ok = bool(create_or_update_autostart_task(executable))
        except Exception:
            ok = False
        if ok:
            _status("Автозапуск программы включён")
            return GuiAutostartResult(success=True)
        return GuiAutostartResult(
            success=False,
            message=(
                "Не удалось включить автозапуск. Windows не дал создать задачу "
                "в Планировщике заданий."
            ),
        )

    def ensure_current(self, executable: str) -> bool:
        """Fixes autostart switched on by an old version through the Startup shortcut.

        Windows silently ignores such shortcuts for programs with requireAdministrator when UAC is on,
        so the scheduler task is created again and the old shortcut removed. The task is also registered
        again when the exe moved.
        """
        from platforms.windows.scheduled_task import (
            AUTOSTART_TASK_ARGS,
            create_or_update_autostart_task,
            get_autostart_task_action,
        )
        from platforms.windows.startup_shortcut import delete_startup_shortcut, get_startup_shortcut_path

        action = get_autostart_task_action()
        task_ok = (
            action is not None
            and _same_windows_path(action[0], executable)
            and action[1].strip() == AUTOSTART_TASK_ARGS
        )
        shortcut_exists = get_startup_shortcut_path().exists()
        if task_ok and not shortcut_exists:
            return False

        if not task_ok and not create_or_update_autostart_task(executable):
            return False
        delete_startup_shortcut()
        return True
