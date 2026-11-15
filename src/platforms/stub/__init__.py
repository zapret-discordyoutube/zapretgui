"""Backend for systems the program does not support: safe answers, no windows."""

from __future__ import annotations

from platforms.base import MessageKind, Platform, print_to_stderr


class _StderrMessageBox:
    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        print_to_stderr(title, message)


PLATFORM = Platform(name="stub", messages=_StderrMessageBox())
