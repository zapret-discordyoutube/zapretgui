"""Backend for systems the program does not support: safe answers, no windows."""

from __future__ import annotations

import threading
from typing import Callable, Sequence

from platforms.base import InstanceLock, MessageKind, Platform, print_to_stderr


class _StderrMessageBox:
    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        print_to_stderr(title, message)


class _NoPrivileges:
    gui_needs_elevation = False

    def is_elevated(self) -> bool:
        return False

    def relaunch_elevated(self, executable: str, args: Sequence[str]) -> bool:
        return False


class _NoSingleInstance:
    def acquire(self, name: str) -> InstanceLock:
        return InstanceLock(already_running=False)

    def create_show_signal(self) -> bool:
        return False

    def signal_running_instance(self) -> bool:
        return False

    def start_show_watcher(
        self, callback: Callable[[], None], *, wait_fn: Callable[[], int] | None = None
    ) -> threading.Thread | None:
        return None


PLATFORM = Platform(
    name="stub",
    messages=_StderrMessageBox(),
    privileges=_NoPrivileges(),
    single_instance=_NoSingleInstance(),
)
