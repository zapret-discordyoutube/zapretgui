"""Windows backend. The only place where Windows-only calls are allowed."""

from __future__ import annotations

from platforms.base import Platform
from platforms.windows.messages import WindowsMessageBox

PLATFORM = Platform(name="windows", messages=WindowsMessageBox())
