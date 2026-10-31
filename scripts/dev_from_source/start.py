"""Start Zapret on the Windows VM from the current sources (developer stand).

Edit the code on the Linux host, close Zapret (tray -> Exit), start it again
with the "Zapret (sources)" desktop shortcut: the new code runs. No build.

How a launch goes:

1. This script runs as the normal user, from the share
   ``G:\\public_zapretgui\\scripts\\dev_from_source\\start.py``. Only the normal
   user sees ``G:``: the app itself runs as administrator, and Windows hides
   drives mapped by the normal user from administrator programs.
2. It copies ``G:\\public_zapretgui\\src`` into a local folder (only changed
   files are copied; a local copy also starts faster than imports over the
   network).
3. It starts the scheduled task ``ZapretFromSource``. The task runs
   ``run_app.py`` with administrator rights and without a UAC prompt.
4. ``run_app.py`` makes the sources look like the installed program in
   ``C:\\Zapret\\Dev`` (same settings, logs, presets, engine) and runs
   ``src/main.py``.

This is not a release check: Nuitka-only problems are visible only in a real
build. ``install.py`` registers the tasks and the shortcut once.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_SRC = HERE.parents[1] / "src"
LOCAL_ROOT = Path(os.environ["LOCALAPPDATA"]) / "ZapretGUI" / "dev_from_source"
RUN_TASK = "ZapretFromSource"
APP_MUTEX = "ZapretSingleInstance"

SYNCHRONIZE = 0x00100000
ERROR_ACCESS_DENIED = 5
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
CREATE_NO_WINDOW = 0x08000000


def _message(text: str, icon: int) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "Zapret (sources)", icon)


def app_is_running() -> bool:
    """True when any Zapret (installed or from sources) holds its mutex."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, APP_MUTEX)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # The app runs as administrator, so a normal user may be refused access
    # to its mutex: refused still means the mutex exists.
    return ctypes.get_last_error() == ERROR_ACCESS_DENIED


def _run_hidden(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


def sync_sources() -> None:
    # /MIR mirrors the folder (deleted files are deleted locally too);
    # __pycache__ is excluded both ways, so Windows keeps its own compiled cache.
    result = _run_hidden(
        [
            "robocopy",
            str(SOURCE_SRC),
            str(LOCAL_ROOT / "src"),
            "/MIR",
            "/XD",
            "__pycache__",
            "/R:2",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NP",
        ]
    )
    if result.returncode >= 8:  # robocopy: 0-7 are success codes
        raise RuntimeError(f"robocopy failed ({result.returncode}):\n{result.stdout[-1500:]}")
    shutil.copy2(HERE / "run_app.py", LOCAL_ROOT / "run_app.py")


def main() -> int:
    if app_is_running():
        _message(
            "Zapret is already running.\n\n"
            "Close it first (tray icon -> Exit), then start it again.",
            MB_ICONWARNING,
        )
        return 1
    try:
        LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
        sync_sources()
        result = _run_hidden(["schtasks", "/run", "/tn", RUN_TASK])
        if result.returncode != 0:
            raise RuntimeError(
                f"Could not start the task {RUN_TASK}. Run install.py once.\n\n"
                f"{result.stdout}{result.stderr}"
            )
    except Exception as exc:
        _message(f"Could not start Zapret from sources:\n\n{exc}", MB_ICONERROR)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
