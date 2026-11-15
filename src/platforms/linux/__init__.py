"""Linux backend."""

from __future__ import annotations

from platforms.base import Platform
from platforms.linux.messages import LinuxMessageBox

PLATFORM = Platform(name="linux", messages=LinuxMessageBox())
