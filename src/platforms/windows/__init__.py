"""Windows backend. The only place where Windows-only calls are allowed."""

from __future__ import annotations

from platforms.base import Cap, Platform
from platforms.windows.messages import WindowsMessageBox
from platforms.windows.privileges import WindowsPrivileges
from platforms.windows.services import WindowsServices
from platforms.windows.single_instance import WindowsSingleInstance

PLATFORM = Platform(
    name="windows",
    messages=WindowsMessageBox(),
    privileges=WindowsPrivileges(),
    single_instance=WindowsSingleInstance(),
    services=WindowsServices(),
    capabilities=frozenset({Cap.BFE}),
)
