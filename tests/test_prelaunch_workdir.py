from __future__ import annotations

from unittest.mock import patch

import pytest


def test_workdir_failure_shows_window_and_stops_startup() -> None:
    from main import prelaunch

    with (
        patch.object(prelaunch.os, "chdir", side_effect=PermissionError("Отказано в доступе")),
        patch.object(prelaunch, "_show_fatal_startup_error") as show_error,
    ):
        with pytest.raises(SystemExit) as exit_info:
            prelaunch._set_workdir_to_app()

    assert exit_info.value.code == 1
    show_error.assert_called_once()
    message = show_error.call_args.args[0]
    assert str(prelaunch.APPLICATION_PATHS.root) in message
    assert "Отказано в доступе" in message


def test_workdir_success_enters_install_root_without_window() -> None:
    from main import prelaunch

    with (
        patch.object(prelaunch.os, "chdir") as chdir,
        patch.object(prelaunch, "_show_fatal_startup_error") as show_error,
    ):
        prelaunch._set_workdir_to_app()

    chdir.assert_called_once_with(prelaunch.APPLICATION_PATHS.root)
    show_error.assert_not_called()
