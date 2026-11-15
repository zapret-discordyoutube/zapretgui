"""The shared shape of a platform backend.

One ``Platform`` object per running system. The rest of the program asks it for
what it needs (``platform.messages.show(...)``) and never checks "am I on
Windows?" itself. Every new interface of the multi-platform project is a new
field here, plus one implementation per backend (windows, linux, stub).
Plan and file-to-interface map: ``app/PLATFORM_MIGRATION_MAP.md``.

This module must stay tiny and import nothing heavy: the launch gate uses it
before Qt, the logger or any other part of the program exists.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence


class MessageKind(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MessageBox(Protocol):
    """A blocking message for the person at the screen, usable before Qt starts."""

    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        """Shows the message and returns after it is closed. Never raises."""


class Privileges(Protocol):
    """Administrator / root rights of the running program."""

    @property
    def gui_needs_elevation(self) -> bool:
        """True when the window itself must run elevated (Windows: the engine needs it).

        On Linux the window runs as a normal user; a root helper service does the
        privileged work, so the program never asks to restart itself as root.
        """

    def is_elevated(self) -> bool:
        """True when the process runs as administrator / root. Never raises."""

    def relaunch_elevated(self, executable: str, args: Sequence[str]) -> bool:
        """Asks the system to start ``executable args`` with higher rights.

        True when the request was accepted (the caller then exits); False when it
        was refused or is not possible on this system.
        """


class Services(Protocol):
    """The system's background services (Windows services, systemd units)."""

    def is_running(self, name: str) -> bool:
        """True when the service exists and runs. A service that does not exist is "not running"."""

    def start(self, name: str, timeout: float = 10) -> bool:
        """Tries to start the service and waits up to ``timeout`` seconds. True when it runs afterwards."""


@dataclass(frozen=True, slots=True)
class SystemSupport:
    """Whether the running system is new enough for the program."""

    supported: bool
    os_name: str = ""
    title: str = ""  # title of the window that tells the person (empty when supported)
    message: str = ""  # the text of that window, shown as is


class Launch(Protocol):
    """What the very first start needs to know about the system. Used before Qt and the logger exist."""

    def process_creation_unix_seconds(self) -> float | None:
        """When the operating system created this process (Unix seconds); None when unknown. Never raises."""

    def system_support(self) -> SystemSupport:
        """Whether this version of the system is supported. Never raises; an unknown version counts as supported."""

    def source_launch_message(self) -> str:
        """Why starting from source is refused and what to do instead, for the person who tried."""


class Cap:
    """Names of optional features. ``Platform.has(Cap.X)`` tells whether this system has the feature.

    Features that exist on one system only (Windows Defender, the BFE firewall service ...) are
    switched off by their capability instead of failing; the interface hides them.
    """

    BFE = "windows_bfe"  # Base Filtering Engine: the Windows service WinDivert depends on
    SELF_UPDATE = "self_update"  # the program replaces itself from a downloaded installer
    WINDOWS_STARTUP_CHECKS = "windows_startup_checks"  # advisories about Windows software and settings at start


WAIT_SIGNALLED = 0
"""What a ``wait_fn`` returns when the "show window" signal arrived. Any other value ends the watcher."""


@dataclass(slots=True)
class InstanceLock:
    """Result of ``SingleInstance.acquire``. Keep it until the program exits."""

    already_running: bool
    _release: Callable[[], None] = lambda: None

    def release(self) -> None:
        self._release()


class SingleInstance(Protocol):
    """Only one copy of the program runs; a second copy asks the first to show its window.

    Order at start: ``acquire`` -> (second copy: ``signal_running_instance`` and exit) ->
    first copy: ``create_show_signal`` before Qt starts, so a signal sent while the
    first copy is still loading is not lost -> later ``start_show_watcher``.
    """

    def acquire(self, name: str) -> InstanceLock:
        """Takes the single-instance lock. If the system refuses, returns a lock that is not "already running"."""

    def create_show_signal(self) -> bool:
        """First copy: makes the "show window" signal receivable. False if impossible."""

    def signal_running_instance(self) -> bool:
        """Second copy: asks the running copy to show its window. False when nobody received it."""

    def start_show_watcher(
        self,
        callback: Callable[[], None],
        *,
        wait_fn: Callable[[], int] | None = None,
    ) -> threading.Thread | None:
        """Starts a daemon thread calling ``callback()`` for every signal; None if the signal was never created.

        ``wait_fn`` is a test seam replacing the real wait.
        """


def run_show_watcher(callback: Callable[[], None], wait_fn: Callable[[], int]) -> threading.Thread:
    """The loop every backend shares: wait, call back, repeat until the wait fails."""

    def _watch() -> None:
        while wait_fn() == WAIT_SIGNALLED:
            callback()

    thread = threading.Thread(target=_watch, daemon=True, name="show-event-watcher")
    thread.start()
    return thread


@dataclass(frozen=True, slots=True)
class Platform:
    name: str
    messages: MessageBox
    privileges: Privileges
    single_instance: SingleInstance
    services: Services
    launch: Launch
    capabilities: frozenset[str] = frozenset()

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


def print_to_stderr(title: str, message: str) -> None:
    """The last resort when there is no way to show a window."""
    import sys

    print(f"{title}\n\n{message}", file=sys.stderr)
