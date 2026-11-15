"""Single-instance on Linux: a lock file (flock) finds the second start, a Unix
socket carries "show the window" to the running copy.

The lock dies with the process, so a crash never leaves a stale lock. The socket
is bound before Qt starts; connections wait in its queue, so a signal sent while
the first copy is still loading is delivered once the watcher starts.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
from pathlib import Path
from typing import Callable

from platforms.base import WAIT_SIGNALLED, InstanceLock, run_show_watcher

SHOW_SOCKET_NAME = "ZapretGUI_ShowWindow.sock"
_WAIT_FAILED = 1


def runtime_dir() -> Path:
    """XDG_RUNTIME_DIR (per user, cleaned at logout), else a private folder in /tmp."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and os.path.isdir(base):
        return Path(base)
    path = Path(tempfile.gettempdir()) / f"zapretgui-{os.getuid()}"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


class LinuxSingleInstance:
    def __init__(self) -> None:
        self._listener: socket.socket | None = None

    def acquire(self, name: str) -> InstanceLock:
        import fcntl

        try:
            handle = open(runtime_dir() / f"{name}.lock", "a+")
        except OSError:
            return InstanceLock(already_running=False)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return InstanceLock(already_running=True)
        except OSError:
            handle.close()
            return InstanceLock(already_running=False)
        # The open file keeps the lock; closing it (release or exit) frees it.
        return InstanceLock(already_running=False, _release=handle.close)

    def create_show_signal(self) -> bool:
        if self._listener is not None:
            return True
        path = runtime_dir() / SHOW_SOCKET_NAME
        try:
            # We hold the lock, so an existing socket file is a leftover of a dead copy.
            path.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.listen(8)
        except OSError:
            return False
        self._listener = listener
        return True

    def signal_running_instance(self) -> bool:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(runtime_dir() / SHOW_SOCKET_NAME))
                client.sendall(b"show")
            return True
        except OSError:
            return False

    def start_show_watcher(
        self,
        callback: Callable[[], None],
        *,
        wait_fn: Callable[[], int] | None = None,
    ) -> threading.Thread | None:
        if wait_fn is None:
            listener = self._listener
            if listener is None:
                return None

            def wait_fn() -> int:
                try:
                    connection, _ = listener.accept()
                except OSError:
                    return _WAIT_FAILED
                connection.close()
                return WAIT_SIGNALLED

        return run_show_watcher(callback, wait_fn)
