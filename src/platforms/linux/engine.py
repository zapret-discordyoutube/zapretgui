from __future__ import annotations

import threading

from platforms.linux.runner import LinuxStrategyRunner


class LinuxEngine:
    """One runner for the root helper. The engine program path the caller passes is only an identity here:
    the helper starts its own fixed ``nfqws2``."""

    def __init__(self) -> None:
        self._runner: LinuxStrategyRunner | None = None
        self._lock = threading.Lock()

    def runner(self, engine_exe_path: str):
        with self._lock:
            if self._runner is None:
                self._runner = LinuxStrategyRunner(engine_exe_path)
            return self._runner

    def current_runner(self):
        return self._runner

    def reset_runner(self) -> None:
        with self._lock:
            runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop()

    def invalidate_runner(self) -> None:
        with self._lock:
            runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop_background_watchers()
