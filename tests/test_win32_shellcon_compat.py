from __future__ import annotations

import importlib
import sys
from unittest.mock import patch


def test_installs_only_qframelesswindow_shell_constants(monkeypatch) -> None:
    from main.win32_shellcon_compat import install_win32_shellcon_compat

    for name in (
        "pythoncom",
        "win32com",
        "win32comext.shell.shellcon",
        "win32comext.shell",
        "win32comext",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)

    shellcon = install_win32_shellcon_compat()

    assert importlib.import_module("win32comext.shell.shellcon") is shellcon
    assert shellcon.ABM_GETSTATE == 0x00000004
    assert shellcon.ABM_GETTASKBARPOS == 0x00000005
    assert shellcon.ABS_AUTOHIDE == 0x00000001
    assert install_win32_shellcon_compat() is shellcon
    assert "pythoncom" not in sys.modules
    assert "win32com" not in sys.modules


def test_prelaunch_installs_shell_constants_before_module_preload() -> None:
    from main import prelaunch

    calls: list[str] = []
    prelaunch._PRELAUNCH_DONE = False
    with (
        patch.object(prelaunch, "_set_workdir_to_app", side_effect=lambda: calls.append("workdir")),
        patch.object(prelaunch, "_install_crash_handler", side_effect=lambda: calls.append("crash")),
        patch.object(
            prelaunch,
            "install_pyinstaller_archive_import_lock",
            side_effect=lambda: calls.append("archive-lock"),
        ),
        patch.object(
            prelaunch,
            "install_win32_shellcon_compat",
            side_effect=lambda: calls.append("shellcon"),
        ),
        patch.object(prelaunch, "_preload_slow_modules", side_effect=lambda: calls.append("preload")),
    ):
        prelaunch.prepare_prelaunch()

    assert calls == ["workdir", "crash", "archive-lock", "shellcon", "preload"]
