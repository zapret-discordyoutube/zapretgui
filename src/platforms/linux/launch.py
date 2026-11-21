from __future__ import annotations

import os
import time
import sys

from platforms.base import SystemSupport

DEV_ROOT_ENV = "ZAPRET_DEV_ROOT"
UTF8_RESTART_ENV = "ZAPRET_UTF8_RESTARTED"


def utf8_mode_needed(environ=None) -> bool:
    """True when file names are not UTF-8 and the program has not already restarted itself for that."""
    environ = os.environ if environ is None else environ
    if sys.flags.utf8_mode or environ.get(UTF8_RESTART_ENV):
        return False
    return (sys.getfilesystemencoding() or "").lower().replace("-", "") != "utf8"


def restart_in_utf8_mode() -> None:
    """Replace this process with itself in UTF-8 mode. Returns only when the restart is impossible."""
    environment = dict(os.environ, PYTHONUTF8="1", LC_CTYPE="C.UTF-8", **{UTF8_RESTART_ENV: "1"})
    try:
        os.execve("/proc/self/exe", [sys.argv[0], *sys.argv[1:]], environment)
    except OSError:
        return


class LinuxLaunch:
    @staticmethod
    def _boot_unix_seconds() -> float | None:
        """When the machine booted, to a millisecond.

        ``CLOCK_BOOTTIME`` is the exact time since boot and, unlike /proc/uptime, is not rewritten inside a container.
        ``btime`` in /proc/stat is a whole number of seconds: with it the "process start" was off by up to a second
        (a constant 0.8 s on the test machine), which made the start-up gap in the log look 20 times longer than it is.
        It stays as the fallback."""
        try:
            return time.time() - time.clock_gettime(time.CLOCK_BOOTTIME)
        except (AttributeError, OSError):
            pass
        try:
            with open("/proc/stat", "rb") as handle:
                for line in handle.read().decode("ascii", "replace").splitlines():
                    if line.startswith("btime "):
                        return float(line.split()[1])
        except (OSError, ValueError):
            pass
        return None

    def process_creation_unix_seconds(self) -> float | None:
        """Start time of this process: the kernel counts it in clock ticks since boot (field 22 of /proc/self/stat)."""
        try:
            with open("/proc/self/stat", "rb") as handle:
                raw = handle.read().decode("utf-8", "replace")
            # The command name (field 2) is in brackets and may contain spaces: count after the last ")".
            fields = raw.rsplit(")", 1)[1].split()
            start_ticks = int(fields[19])
            boot_unix = self._boot_unix_seconds()
            if boot_unix is None:
                return None
            return boot_unix + start_ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None

    def prepare_process(self) -> None:
        """Make Python read and write file names and files as UTF-8, whatever the locale says.

        Started from a menu entry, a service or ``env -i`` the locale is often "C", and Python then decodes
        Russian file names (the presets have them) into lone surrogates that cannot be written back. The
        interpreter cannot switch to UTF-8 mode after it started, so the program restarts itself once.
        """
        if utf8_mode_needed():
            restart_in_utf8_mode()

    def system_support(self) -> SystemSupport:
        return SystemSupport(supported=True)

    def source_launch_message(self) -> str:
        return (
            "Запуск приложения из исходников запрещён. Запустите установленный Zapret "
            f"(/opt/zapretgui) или, для разработки, укажите подготовленную папку: {DEV_ROOT_ENV}=/путь "
            f"{sys.executable} src/main.py (папку готовит scripts/prepare_dev_root.py)."
        )
