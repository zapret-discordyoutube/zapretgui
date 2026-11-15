from __future__ import annotations

import ctypes
import subprocess
from typing import Sequence


class WindowsPrivileges:
    gui_needs_elevation = True  # winws2 / WinDivert need administrator rights

    def is_elevated(self) -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def relaunch_elevated(self, executable: str, args: Sequence[str]) -> bool:
        # "runas" is the verb that shows the UAC question. The program starts only
        # from the built Zapret.exe, so ``executable`` is always that file.
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, subprocess.list2cmdline(list(args)), None, 1
        )
        return int(result) > 32
