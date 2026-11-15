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
from dataclasses import dataclass
from typing import Protocol


class MessageKind(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MessageBox(Protocol):
    """A blocking message for the person at the screen, usable before Qt starts."""

    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        """Shows the message and returns after it is closed. Never raises."""


@dataclass(frozen=True, slots=True)
class Platform:
    name: str
    messages: MessageBox


def print_to_stderr(title: str, message: str) -> None:
    """The last resort when there is no way to show a window."""
    import sys

    print(f"{title}\n\n{message}", file=sys.stderr)
