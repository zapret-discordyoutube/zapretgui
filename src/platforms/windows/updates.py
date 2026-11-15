"""Release files on Windows: the Inno Setup installer; the program replaces itself (``Cap.SELF_UPDATE``)."""

from __future__ import annotations

from pathlib import Path


class WindowsUpdates:
    platform_key = "windows"
    package_suffix = ".exe"

    def install_package(self, path: Path) -> tuple[bool, str]:
        return False, "On Windows the installer is started by the update flow of the program."
