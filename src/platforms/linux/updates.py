"""Release files on Linux: the .deb package. The program never replaces itself; the system's package manager installs it."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

INSTALL_TIMEOUT_S = 900


def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(list(command), capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, (done.stderr or done.stdout)


class LinuxUpdates:
    platform_key = "linux"
    package_suffix = ".deb"

    def __init__(self, run: Callable[[Sequence[str]], tuple[int, str]] = _run, which=shutil.which) -> None:
        self._run = run
        self._which = which

    def install_package(self, path: Path) -> tuple[bool, str]:
        """``pkexec apt-get install``: the system asks for the password in its own window, apt resolves the dependencies."""
        package = Path(path)
        manual = f"sudo apt install {package}"
        if package.suffix != self.package_suffix or not package.is_file():
            return False, f"This is not a package file: {package}"
        if not self._which("pkexec"):
            return False, f"pkexec is not installed. Install the package by hand: {manual}"
        code, text = self._run(["pkexec", "apt-get", "install", "-y", str(package)])
        if code == 0:
            return True, "The new version is installed. Close and start Zapret again."
        if code in (126, 127):
            return False, f"The password window was closed or refused. You can also install by hand: {manual}"
        tail = " ".join(text.strip().splitlines()[-2:])[:300]
        return False, f"The installation failed ({code}): {tail} You can also install by hand: {manual}"
