from __future__ import annotations

import os
import shutil
import subprocess

from platforms.base import MessageKind, print_to_stderr

# (program, arguments that put the message on screen). The first one found wins.
_DIALOG_PROGRAMS = (
    ("zenity", {MessageKind.INFO: "--info", MessageKind.WARNING: "--warning", MessageKind.ERROR: "--error"}),
    ("kdialog", {MessageKind.INFO: "--msgbox", MessageKind.WARNING: "--sorry", MessageKind.ERROR: "--error"}),
)


class LinuxMessageBox:
    """A window from zenity or kdialog when a desktop is there, else the terminal."""

    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            for program, flags in _DIALOG_PROGRAMS:
                path = shutil.which(program)
                if not path:
                    continue
                if program == "zenity":
                    command = [path, flags[kind], "--title", title, "--no-markup", "--text", message]
                else:
                    command = [path, flags[kind], message, "--title", title]
                try:
                    subprocess.run(command, check=False)
                    return
                except OSError:
                    continue
        print_to_stderr(title, message)
