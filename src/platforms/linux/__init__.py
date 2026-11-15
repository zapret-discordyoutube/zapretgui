"""Linux backend."""

from __future__ import annotations

from platforms.base import Cap, NoWorkingSet, Platform
from platforms.linux.autostart import LinuxGuiAutostart
from platforms.linux.desktop import LinuxDesktop
from platforms.linux.dns import LinuxDns
from platforms.linux.engine import LinuxEngine
from platforms.linux.hosts_file import LinuxHostsFile
from platforms.linux.launch import LinuxLaunch
from platforms.linux.messages import LinuxMessageBox
from platforms.linux.netprobe import LinuxNetProbe
from platforms.linux.system_settings import LinuxSystemSettings
from platforms.linux.tray_factory import LinuxTray
from platforms.linux.updates import LinuxUpdates
from platforms.linux.privileges import LinuxPrivileges
from platforms.linux.processes import LinuxProcesses
from platforms.linux.services import LinuxServices
from platforms.linux.single_instance import LinuxSingleInstance

PLATFORM = Platform(
    name="linux",
    messages=LinuxMessageBox(),
    privileges=LinuxPrivileges(),
    single_instance=LinuxSingleInstance(),
    services=LinuxServices(),
    launch=LinuxLaunch(),
    processes=LinuxProcesses(),
    netprobe=LinuxNetProbe(),
    autostart=LinuxGuiAutostart(),
    desktop=LinuxDesktop(),
    tray=LinuxTray(),
    system_settings=LinuxSystemSettings(),
    dns=LinuxDns(),
    hosts=LinuxHostsFile(),
    updates=LinuxUpdates(),
    engine=LinuxEngine(),
    working_set=NoWorkingSet(),
    capabilities=frozenset({Cap.PACKAGE_UPDATE}),
)
