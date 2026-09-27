from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from config.runtime_layout import (
    ApplicationPaths,
    InvalidInstallLayout,
    SourceApplicationLaunchForbidden,
)
from main import launch_gate
from startup.windows_version_guard import WINDOWS_VERSION_ERROR_TITLE, WindowsSupportResult


def _pass_gate(
    *,
    packaged_error: Exception | None = None,
    layout_error: Exception | None = None,
    windows: WindowsSupportResult | None = None,
    chdir_error: Exception | None = None,
):
    with (
        patch.object(launch_gate, "require_packaged_application", side_effect=packaged_error),
        patch.object(launch_gate, "require_valid_install_layout", side_effect=layout_error),
        patch.object(
            launch_gate,
            "current_windows_support",
            return_value=windows or WindowsSupportResult(supported=True),
        ),
        patch.object(launch_gate.os, "chdir", side_effect=chdir_error) as chdir,
        patch.object(launch_gate, "show_launch_error") as show_error,
    ):
        try:
            launch_gate.pass_launch_gate()
        except SystemExit as exc:
            return exc.code, show_error, chdir
        return None, show_error, chdir


def test_valid_launch_enters_install_root_without_window() -> None:
    code, show_error, chdir = _pass_gate()

    assert code is None
    show_error.assert_not_called()
    chdir.assert_called_once_with(launch_gate.APPLICATION_PATHS.root)


@pytest.mark.parametrize(
    ("kwargs", "title", "text"),
    [
        (
            {"packaged_error": SourceApplicationLaunchForbidden("Запуск из исходников запрещён.")},
            launch_gate.SOURCE_LAUNCH_TITLE,
            "исходников",
        ),
        (
            {"layout_error": InvalidInstallLayout("Некорректная структура")},
            launch_gate.INSTALL_LAYOUT_TITLE,
            "_internal",
        ),
        (
            {"windows": WindowsSupportResult(supported=False, os_name="Windows 7", message="Нужна Windows 10 1809")},
            WINDOWS_VERSION_ERROR_TITLE,
            "Windows 10 1809",
        ),
        (
            {"chdir_error": PermissionError("Отказано в доступе")},
            launch_gate.INSTALL_ROOT_TITLE,
            "Отказано в доступе",
        ),
    ],
)
def test_each_blocked_launch_shows_one_window_and_exits(kwargs, title, text) -> None:
    code, show_error, _chdir = _pass_gate(**kwargs)

    assert code == 1
    show_error.assert_called_once()
    shown_title, shown_message = show_error.call_args.args
    assert shown_title == title
    assert text in shown_message


def test_blocked_launch_does_not_enter_install_root() -> None:
    _code, _show_error, chdir = _pass_gate(layout_error=InvalidInstallLayout("Некорректная структура"))

    chdir.assert_not_called()


def test_startup_crash_shows_window_with_report_path() -> None:
    def crash() -> None:
        raise RuntimeError("boom")

    with tempfile.TemporaryDirectory() as tmp:
        app_paths = ApplicationPaths.from_root(Path(tmp))
        with (
            patch.object(launch_gate, "APPLICATION_PATHS", app_paths),
            patch.object(launch_gate, "show_launch_error") as show_error,
        ):
            with pytest.raises(SystemExit) as exit_info:
                launch_gate.run_guarded(crash)

        report = app_paths.crash_logs_dir / launch_gate.STARTUP_CRASH_FILE_NAME
        report_text = report.read_text(encoding="utf-8")

    assert exit_info.value.code == 1
    title, message = show_error.call_args.args
    assert title == launch_gate.STARTUP_CRASH_TITLE
    assert "RuntimeError: boom" in message
    assert str(report) in message
    assert "RuntimeError: boom" in report_text


def test_normal_exit_passes_through_without_window() -> None:
    def normal_exit() -> None:
        raise SystemExit(0)

    with patch.object(launch_gate, "show_launch_error") as show_error:
        with pytest.raises(SystemExit) as exit_info:
            launch_gate.run_guarded(normal_exit)

    assert exit_info.value.code == 0
    show_error.assert_not_called()


def test_launch_gate_stays_light_before_qt() -> None:
    source = Path(launch_gate.__file__).read_text(encoding="utf-8")

    assert "PyQt6" not in source
    assert "qfluentwidgets" not in source
    assert "from log" not in source
