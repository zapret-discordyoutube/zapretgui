from __future__ import annotations

import os

from main.pyinstaller_archive_import_lock import install_pyinstaller_archive_import_lock
from main.win32_shellcon_compat import install_win32_shellcon_compat


_PRELAUNCH_DONE = False


def _install_crash_handler() -> None:
    from log.crash_handler import install_crash_handler

    if os.environ.get("ZAPRET_DISABLE_CRASH_HANDLER") != "1":
        install_crash_handler()


def _preload_slow_modules() -> None:
    import threading

    def _preload() -> None:
        # qtawesome здесь НЕ греем: на этой фазе главный поток занят
        # импортами PyQt6/qfluentwidgets, и фоновый импорт qtawesome
        # отбирает у него GIL. Прогрев qtawesome стартует позже —
        # см. start_qtawesome_warmup() в main.entry.
        try:
            import requests
            import psutil
            import json
            import winreg
        except Exception:
            pass

    thread = threading.Thread(target=_preload, daemon=True)
    thread.start()


def prepare_prelaunch() -> None:
    global _PRELAUNCH_DONE
    if _PRELAUNCH_DONE:
        return

    _install_crash_handler()
    install_pyinstaller_archive_import_lock()
    install_win32_shellcon_compat()
    _preload_slow_modules()
    _PRELAUNCH_DONE = True
