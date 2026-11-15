"""Backend for systems the program does not support: safe answers, no windows."""

from __future__ import annotations

import threading
from typing import Callable, Sequence

from platforms.base import (
    InstanceLock,
    MessageKind,
    NoWorkingSet,
    Platform,
    ProcessSnapshotError,
    SystemSupport,
    print_to_stderr,
)


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


class _NoServices:
    def is_running(self, name: str) -> bool:
        return False

    def start(self, name: str, timeout: float = 10) -> bool:
        return False


class _NoProcesses:
    def list_processes(self) -> list[tuple[int, str]]:
        return []

    def list_processes_strict(self) -> list[tuple[int, str]]:
        raise ProcessSnapshotError("no process access on this system")

    def command_line(self, pid: int) -> str:
        return ""

    def exe_path(self, pid: int) -> str:
        return ""

    def module_paths(self, pid: int, *, max_snapshot_attempts: int = 3) -> list[str]:
        return []

    def start_time(self, pid: int) -> float:
        return 0.0

    def kill(self, pid: int, wait_timeout_ms: int = 3000) -> bool:
        return False


class _NoLaunch:
    def process_creation_unix_seconds(self) -> float | None:
        return None

    def system_support(self) -> SystemSupport:
        return SystemSupport(supported=True)

    def source_launch_message(self) -> str:
        return "Запуск приложения из исходников запрещён."


PLATFORM = Platform(
    name="stub",
    messages=_StderrMessageBox(),
    privileges=_NoPrivileges(),
    single_instance=_NoSingleInstance(),
    services=_NoServices(),
    launch=_NoLaunch(),
    processes=_NoProcesses(),
    working_set=NoWorkingSet(),
)
