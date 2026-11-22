"""Release files on Linux: the .deb package. The program never replaces itself; the system's package manager installs it.

The texts returned here are shown to the person in the window (a toast), whose language is Russian."""

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
            return False, f"Это не файл пакета: {package}"
        if not self._which("pkexec"):
            return False, f"Не установлена программа pkexec. Установите пакет вручную: {manual}"
        if not self._which("apt-get"):
            # Arch, Fedora and others: the .deb is not their format. Say so instead of a password error that is not one.
            return False, (
                "Эта система ставит пакеты не через apt, а файл обновления — пакет .deb. "
                "Скачайте со страницы выпуска свежий AppImage (запустите его — он обновит программу) "
                "или архив zapretgui-…-linux-x86_64.tar.gz и выполните в нём sudo ./install.sh."
            )
        code, text = self._run(["pkexec", "apt-get", "install", "-y", str(package)])
        if code == 0:
            return True, "Новая версия установлена. Закройте Zapret и запустите снова."
        if code in (126, 127):
            return False, f"Окно пароля закрыто или пароль не принят. Пакет можно поставить и вручную: {manual}"
        tail = " ".join(text.strip().splitlines()[-2:])[:300]
        return False, f"Установка не удалась (код {code}): {tail} Пакет можно поставить и вручную: {manual}"
