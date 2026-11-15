"""Single-instance on Windows: a named mutex finds the second start, a named
auto-reset event carries "show the window" to the running copy.

The event is created right after the mutex, before Qt starts, so a signal from a
second copy started during loading is not lost (an auto-reset event stays
signalled until a wait consumes it).
"""

from __future__ import annotations

import threading
from typing import Callable

from platforms.base import WAIT_SIGNALLED, InstanceLock, run_show_watcher

ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF

SHOW_EVENT_NAME = "ZapretGUI_ShowWindowEvent"

assert WAIT_OBJECT_0 == WAIT_SIGNALLED


def _kernel32():
    import ctypes

    return ctypes.windll.kernel32


class WindowsSingleInstance:
    def __init__(self) -> None:
        self._show_event_handle: int | None = None

    def acquire(self, name: str) -> InstanceLock:
        kernel32 = _kernel32()
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, name)
        last_error = kernel32.GetLastError()
        if not handle:
            return InstanceLock(already_running=False)

        def release() -> None:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)

        return InstanceLock(already_running=last_error == ERROR_ALREADY_EXISTS, _release=release)

    def create_show_signal(self) -> bool:
        """The handle lives until the end of the process and the OS closes it at exit:
        an explicit CloseHandle while the watcher thread waits on it is undefined
        behaviour, and the watcher is a daemon thread that dies with the process.
        """
        if self._show_event_handle:
            return True
        handle = _kernel32().CreateEventW(None, False, False, SHOW_EVENT_NAME)
        self._show_event_handle = int(handle) if handle else None
        return self._show_event_handle is not None

    def signal_running_instance(self) -> bool:
        """False: the event does not exist (the first copy is an old version) or SetEvent failed."""
        kernel32 = _kernel32()
        handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
        if not handle:
            return False
        try:
            return bool(kernel32.SetEvent(handle))
        finally:
            kernel32.CloseHandle(handle)

    def start_show_watcher(
        self,
        callback: Callable[[], None],
        *,
        wait_fn: Callable[[], int] | None = None,
    ) -> threading.Thread | None:
        if wait_fn is None:
            handle = self._show_event_handle
            if not handle:
                return None
            kernel32 = _kernel32()

            def wait_fn() -> int:
                return kernel32.WaitForSingleObject(handle, _INFINITE)

        return run_show_watcher(callback, wait_fn)
