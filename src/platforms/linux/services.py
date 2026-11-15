from __future__ import annotations

import shutil
import subprocess


class LinuxServices:
    """systemd units. Starting a unit needs root, which is the helper service's job later."""

    def is_running(self, name: str) -> bool:
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return False
        try:
            done = subprocess.run(
                [systemctl, "is-active", "--quiet", name],
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
