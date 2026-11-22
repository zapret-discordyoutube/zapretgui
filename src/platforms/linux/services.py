from __future__ import annotations

import shutil
import subprocess

from platforms.linux import init_system


class LinuxServices:
    """Services of the init system found on this machine (systemd, OpenRC, sysvinit, runit). Starting one needs root, which is the helper service's job."""

    def is_running(self, name: str) -> bool:
        commands = {
            init_system.SYSTEMD: ["systemctl", "is-active", "--quiet", name],
            init_system.OPENRC: ["rc-service", name, "status"],
            init_system.SYSV: ["service", name, "status"],
            init_system.RUNIT: ["sv", "check", name],
        }
        command = commands.get(init_system.detect())
        if not command:
            return False
        program = shutil.which(command[0])
        if not program:
            return False
        try:
            done = subprocess.run(
                [program, *command[1:]],
                check=False,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0

    def start(self, name: str, timeout: float = 10) -> bool:
        return False
