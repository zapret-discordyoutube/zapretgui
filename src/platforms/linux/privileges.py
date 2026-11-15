from __future__ import annotations

import os
from typing import Sequence


class LinuxPrivileges:
    # The window runs as a normal user. The privileged work (firewall rules, the
    # engine) belongs to a root helper service, so the window never restarts itself as root.
    gui_needs_elevation = False

    def is_elevated(self) -> bool:
        return os.geteuid() == 0

    def relaunch_elevated(self, executable: str, args: Sequence[str]) -> bool:
        return False
