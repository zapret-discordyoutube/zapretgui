from __future__ import annotations

import os

from config.runtime_layout import APPLICATION_PATHS
from main.pyinstaller_archive_import_lock import install_pyinstaller_archive_import_lock
from main.win32_shellcon_compat import install_win32_shellcon_compat


_PRELAUNCH_DONE = False


def _show_fatal_startup_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Zapret — ошибка запуска", 0x10)
    except Exception:
        print(message)


def _set_workdir_to_app() -> None:
    """Делает папку установки рабочей: от неё winws2 ищет @bin/, @lua/, =lists/."""
    app_dir = APPLICATION_PATHS.root
    try:
        os.chdir(app_dir)
    except OSError as exc:
        _show_fatal_startup_error(
            "Zapret не может открыть свою папку установки:\n"
            f"{app_dir}\n\n"
            f"Причина: {exc}\n\n"
            "Проверьте, что у вашей учётной записи есть доступ к этой папке. "
            "Если путь очень длинный, переустановите Zapret в короткую папку, "
            "например C:\\Zapret."
        )
        raise SystemExit(1) from exc


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

    _set_workdir_to_app()
    _install_crash_handler()
    install_pyinstaller_archive_import_lock()
    install_win32_shellcon_compat()
    _preload_slow_modules()
    _PRELAUNCH_DONE = True
