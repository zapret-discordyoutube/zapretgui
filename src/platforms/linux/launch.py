from __future__ import annotations

import os
import sys

from platforms.base import SystemSupport

DEV_ROOT_ENV = "ZAPRET_DEV_ROOT"


class LinuxLaunch:
    def process_creation_unix_seconds(self) -> float | None:
        """Start time of this process: the kernel counts it in clock ticks since boot (field 22 of /proc/self/stat)."""
        try:
            with open("/proc/self/stat", "rb") as handle:
                raw = handle.read().decode("utf-8", "replace")
            # The command name (field 2) is in brackets and may contain spaces: count after the last ")".
            fields = raw.rsplit(")", 1)[1].split()
            start_ticks = int(fields[19])
            # Boot time from "btime" in /proc/stat (what psutil uses too). /proc/uptime would be wrong
            # inside a container: it shows the container's uptime while the ticks count from host boot.
            boot_unix = None
            with open("/proc/stat", "rb") as handle:
                for line in handle.read().decode("ascii", "replace").splitlines():
                    if line.startswith("btime "):
                        boot_unix = float(line.split()[1])
                        break
            if boot_unix is None:
                return None
            return boot_unix + start_ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None

    def system_support(self) -> SystemSupport:
        return SystemSupport(supported=True)

    def source_launch_message(self) -> str:
        return (
            "Запуск приложения из исходников запрещён. Запустите установленный Zapret "
            f"(/opt/zapretgui) или, для разработки, укажите подготовленную папку: {DEV_ROOT_ENV}=/путь "
            f"{sys.executable} src/main.py (папку готовит scripts/prepare_dev_root.py)."
        )
