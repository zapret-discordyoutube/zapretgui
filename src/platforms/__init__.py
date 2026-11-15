"""Platform layer: everything that differs between Windows, Linux and others.

``current()`` returns the backend of the running system, chosen once from
``sys.platform``. The imports below are written out (not looked up by name) so
the Nuitka build follows them and bundles the backend.
"""

from __future__ import annotations

import sys

from platforms.base import MessageBox, MessageKind, Platform

_current: Platform | None = None


def _select() -> Platform:
    if sys.platform == "win32":
        from platforms.windows import PLATFORM
    elif sys.platform.startswith("linux"):
        from platforms.linux import PLATFORM
    else:
        from platforms.stub import PLATFORM
    return PLATFORM


def current() -> Platform:
    global _current
    if _current is None:
        _current = _select()
    return _current


__all__ = ["MessageBox", "MessageKind", "Platform", "current"]
