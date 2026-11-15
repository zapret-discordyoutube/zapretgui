"""Windows backend. The only place where Windows-only calls are allowed."""

from __future__ import annotations

from platforms.base import Cap, Platform
from platforms.windows.autostart import WindowsGuiAutostart
from platforms.windows.desktop import WindowsDesktop
from platforms.windows.launch import WindowsLaunch
from platforms.windows.messages import WindowsMessageBox
from platforms.windows.netprobe import WindowsNetProbe
from platforms.windows.system_settings import WindowsSystemSettings
from platforms.windows.tray_factory import WindowsTray
from platforms.windows.privileges import WindowsPrivileges
from platforms.windows.processes import WindowsProcesses, WindowsWorkingSet
from platforms.windows.services import WindowsServices
from platforms.windows.single_instance import WindowsSingleInstance

PLATFORM = Platform(
    name="windows",
    messages=WindowsMessageBox(),
    privileges=WindowsPrivileges(),
    single_instance=WindowsSingleInstance(),
    services=WindowsServices(),
    launch=WindowsLaunch(),
    processes=WindowsProcesses(),
    netprobe=WindowsNetProbe(),
    autostart=WindowsGuiAutostart(),
    desktop=WindowsDesktop(),
    tray=WindowsTray(),
    system_settings=WindowsSystemSettings(),
    working_set=WindowsWorkingSet(),
    capabilities=frozenset({Cap.BFE, Cap.SELF_UPDATE, Cap.WINDOWS_STARTUP_CHECKS, Cap.WORKING_SET}),
)
