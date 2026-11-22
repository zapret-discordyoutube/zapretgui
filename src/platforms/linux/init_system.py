"""Which init system runs this machine, and what a person types to start the helper service under it.

The same decision as ``packaging/linux/initsys.sh`` (which installs the service): systemd, OpenRC, sysvinit, or none (runit, s6,
dinit, a container: the helper is started by hand). ``ZG_INIT`` forces the answer, as it does for the shell code.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

SYSTEMD, OPENRC, SYSV, NONE = "systemd", "openrc", "sysv", "none"

_NO_SERVICE_MANAGER = {"runit", "runit-init", "s6-svscan", "s6-linux-init", "dinit", "finit"}


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def detect() -> str:
    forced = os.environ.get("ZG_INIT", "")
    if forced in (SYSTEMD, OPENRC, SYSV, NONE):
        return forced
    if os.path.isdir("/run/systemd/system"):  # a running systemd makes it; a container image that only has systemctl does not
        return SYSTEMD
    init_name = _read("/proc/1/comm")
    if init_name in _NO_SERVICE_MANAGER:
        return NONE
    if init_name == "openrc-init" or (os.path.exists("/run/openrc/softlevel") and shutil.which("rc-service")):
        return OPENRC
    if os.path.isdir("/etc/init.d") and shutil.which("start-stop-daemon"):
        return SYSV
    return NONE


def start_command() -> str:
    return {
        SYSTEMD: "sudo systemctl start zapretgui-helper",
        OPENRC: "sudo rc-service zapretgui-helper start",
        SYSV: "sudo service zapretgui-helper start",
    }.get(detect(), "sudo /opt/zapretgui/bin/zapretgui-helper")


def status_command() -> str:
    return {
        SYSTEMD: "sudo systemctl status zapretgui-helper",
        OPENRC: "sudo rc-service zapretgui-helper status",
        SYSV: "sudo service zapretgui-helper status",
    }.get(detect(), "pgrep -a -f zapret-helper")


def enable_command() -> str:
    return {
        SYSTEMD: "sudo systemctl enable zapretgui-helper",
        OPENRC: "sudo rc-update add zapretgui-helper default",
        SYSV: "sudo update-rc.d zapretgui-helper defaults",
    }.get(detect(), "")
