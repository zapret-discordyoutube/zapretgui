"""Общий разбор действий из списка preset-ов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class UserPresetListActionHandlers:
    activate: Callable[[str], object]
    open: Callable[[str], object]
    pin: Callable[[str], object]
    rating: Callable[[str], object]
    move_by_step: Callable[[str, int], object]
    edit: Callable[[str], object]
    rename: Callable[[str], object]
    duplicate: Callable[[str], object]
    reset: Callable[[str], object]
    delete: Callable[[str], object]
    export: Callable[[str], object]
    toggle_folder: Callable[[str], object] | None = None
    update_remote: Callable[[str], object] | None = None
    unlink_remote: Callable[[str], object] | None = None


def dispatch_user_preset_list_action(*, action: str, name: str, handlers: UserPresetListActionHandlers) -> None:
    callbacks = {
        "activate": handlers.activate,
        "open": handlers.open,
        "pin": handlers.pin,
        "rating": handlers.rating,
        "move_up": lambda preset_name: handlers.move_by_step(preset_name, -1),
        "move_down": lambda preset_name: handlers.move_by_step(preset_name, 1),
        "edit": handlers.edit,
        "rename": handlers.rename,
        "duplicate": handlers.duplicate,
        "reset": handlers.reset,
        "delete": handlers.delete,
        "export": handlers.export,
    }
    if handlers.toggle_folder is not None:
        callbacks["toggle_folder"] = handlers.toggle_folder
    if handlers.update_remote is not None:
        callbacks["update_remote"] = handlers.update_remote
    if handlers.unlink_remote is not None:
        callbacks["unlink_remote"] = handlers.unlink_remote
    callback = callbacks.get(action)
    if callback is not None:
        callback(name)
