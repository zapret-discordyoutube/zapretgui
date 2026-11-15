"""Linux backend."""

from __future__ import annotations

from platforms.base import Platform
from platforms.linux.launch import LinuxLaunch
from platforms.linux.messages import LinuxMessageBox
from platforms.linux.privileges import LinuxPrivileges
from platforms.linux.services import LinuxServices
from platforms.linux.single_instance import LinuxSingleInstance

PLATFORM = Platform(
    name="linux",
    messages=LinuxMessageBox(),
    privileges=LinuxPrivileges(),
    single_instance=LinuxSingleInstance(),
    services=LinuxServices(),
    launch=LinuxLaunch(),
)
