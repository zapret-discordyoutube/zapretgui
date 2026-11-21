"""Windows backend. The only place where Windows-only calls are allowed."""

from __future__ import annotations

from platforms.base import Cap, Platform
from platforms.windows.autostart import WindowsGuiAutostart
from platforms.windows.desktop import WindowsDesktop
from platforms.windows.dns_adapters import WindowsDns
from platforms.windows.engine import WindowsEngine
from platforms.windows.hosts_file import WindowsHostsFile
from platforms.windows.launch import WindowsLaunch
from platforms.windows.messages import WindowsMessageBox
from platforms.windows.netprobe import WindowsNetProbe
from platforms.windows.system_settings import WindowsSystemSettings
from platforms.windows.tray_factory import WindowsTray
from platforms.windows.updates import WindowsUpdates
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
    dns=WindowsDns(),
    hosts=WindowsHostsFile(),
    updates=WindowsUpdates(),
    engine=WindowsEngine(),
    working_set=WindowsWorkingSet(),
    capabilities=frozenset(
        {
            Cap.BFE,
            Cap.SELF_UPDATE,
            Cap.WINDOWS_STARTUP_CHECKS,
            Cap.WORKING_SET,
            Cap.LEGACY_ENGINE_MODES,
            Cap.DEFENDER,
            Cap.MAX_BLOCKER,
            Cap.ANTIVIRUS_PROBE,
            Cap.NETWORK_RESET,
            Cap.LOCAL_DNS_PROXY,
            Cap.DIRECT_ENGINE_PROCESS,
            Cap.BROWSER_POLICY_SWITCH,
        }
    ),
)
