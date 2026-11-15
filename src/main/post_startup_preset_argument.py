"""A preset file named on the command line (``zapretgui preset.zip``) is imported once the window is ready.

It is the same path as dropping the file on the window: the current page decides what to do with it. Words that start
with ``-`` are program switches, never files; only existing ``.txt`` and ``.zip`` files count (the formats of presets).
"""

from __future__ import annotations

import os
import sys

from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import schedule_after

IMPORT_DELAY_MS = 1200


def preset_paths_from_args(argv: list[str] | None = None) -> list[str]:
    from ui.window_preset_file_drop import valid_preset_file_paths

    args = sys.argv[1:] if argv is None else argv
    return valid_preset_file_paths(os.path.abspath(arg) for arg in args if arg and not arg.startswith("-"))


def install_preset_argument_import(startup_host, *, argv: list[str] | None = None, log_startup_metric=None) -> bool:
    paths = preset_paths_from_args(argv)
    if not paths:
        return False

    def _import() -> None:
        if not is_startup_host_alive(startup_host):
            return
        drop = getattr(startup_host, "_preset_file_drop_filter", None)
        if drop is not None:
            drop.import_file_paths(paths)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        lambda: schedule_after(IMPORT_DELAY_MS, _import),
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )
    return True


__all__ = ["install_preset_argument_import", "preset_paths_from_args"]
