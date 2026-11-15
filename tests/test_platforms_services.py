"""System services behind the platform, and the capability flags that hide Windows-only checks."""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from platforms import Cap
from platforms.base import Platform
from platforms.linux.services import LinuxServices
from platforms.stub import PLATFORM as STUB
from platforms.windows.services import ERROR_SERVICE_DOES_NOT_EXIST, WindowsServices


# --- Windows (pywin32 is faked: it does not exist on Linux) -----------------

class _PyWinError(Exception):
    def __init__(self, winerror):
        super().__init__(winerror)
        self.winerror = winerror


@pytest.fixture()
def fake_win32(monkeypatch):
    module = types.ModuleType("win32service")
    module.SC_MANAGER_CONNECT = 1
    module.SERVICE_QUERY_STATUS = 4
    module.SERVICE_RUNNING = 4
    module.error = _PyWinError
    module.OpenSCManager = MagicMock(return_value="scm")
    module.OpenService = MagicMock(return_value="svc")
    module.QueryServiceStatus = MagicMock(return_value=(0, 4))
    module.CloseServiceHandle = MagicMock()
    util = types.ModuleType("win32serviceutil")
    util.StartService = MagicMock()
    monkeypatch.setitem(sys.modules, "win32service", module)
    monkeypatch.setitem(sys.modules, "win32serviceutil", util)
    return module, util


def test_windows_running_service_is_true_and_handles_are_closed(fake_win32):
    module, _util = fake_win32
    assert WindowsServices().is_running("BFE") is True
    module.OpenService.assert_called_once_with("scm", "BFE", 4)
    assert [c.args[0] for c in module.CloseServiceHandle.call_args_list] == ["svc", "scm"]


def test_windows_stopped_service_is_false(fake_win32):
    module, _util = fake_win32
    module.QueryServiceStatus.return_value = (0, 1)  # SERVICE_STOPPED
    assert WindowsServices().is_running("BFE") is False


def test_windows_missing_service_is_false_and_scm_is_closed(fake_win32):
    module, _util = fake_win32
    module.OpenService.side_effect = _PyWinError(ERROR_SERVICE_DOES_NOT_EXIST)
    assert WindowsServices().is_running("Nope") is False
    module.CloseServiceHandle.assert_called_once_with("scm")


def test_windows_other_service_errors_go_up(fake_win32):
    module, _util = fake_win32
    module.OpenService.side_effect = _PyWinError(5)  # access denied
    with pytest.raises(_PyWinError):
        WindowsServices().is_running("BFE")


def test_windows_start_waits_until_the_service_runs(fake_win32):
    module, util = fake_win32
    states = iter([(0, 2), (0, 4)])  # START_PENDING, then RUNNING
    module.QueryServiceStatus.side_effect = lambda svc: next(states)
    with patch("platforms.windows.services.time.sleep"):
        assert WindowsServices().start("BFE", timeout=5) is True
    util.StartService.assert_called_once_with("BFE")


def test_windows_start_that_never_finishes_times_out(fake_win32):
    module, _util = fake_win32
    module.QueryServiceStatus.return_value = (0, 2)
    clock = iter([0, 0.1, 6, 7, 8])
    with (
        patch("platforms.windows.services.time.time", side_effect=lambda: next(clock)),
        patch("platforms.windows.services.time.sleep"),
    ):
        assert WindowsServices().start("BFE", timeout=5) is False


def test_windows_start_of_an_already_running_service_counts_as_started(fake_win32):
    _module, util = fake_win32
    util.StartService.side_effect = RuntimeError("Cannot start service: already running")
    assert WindowsServices().start("BFE") is True
    util.StartService.side_effect = RuntimeError("access denied")
    assert WindowsServices().start("BFE") is False


def test_importing_the_windows_backend_does_not_load_pywin32():
    code = "import sys, platforms.windows; print('win32service' in sys.modules)"
    src = str(Path(__file__).resolve().parents[1] / "src")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": src})
    assert out.stdout.strip() == "False", out.stderr


# --- Linux ------------------------------------------------------------------

def test_linux_service_state_follows_systemctl():
    with (
        patch("platforms.linux.services.shutil.which", return_value="/bin/systemctl"),
        patch("platforms.linux.services.subprocess.run") as run,
    ):
        run.return_value = MagicMock(returncode=0)
        assert LinuxServices().is_running("nftables") is True
        run.return_value = MagicMock(returncode=3)
        assert LinuxServices().is_running("nftables") is False
    assert run.call_args.args[0] == ["/bin/systemctl", "is-active", "--quiet", "nftables"]


def test_linux_without_systemctl_or_when_it_hangs_is_not_running():
    with patch("platforms.linux.services.shutil.which", return_value=None):
        assert LinuxServices().is_running("x") is False
    with (
        patch("platforms.linux.services.shutil.which", return_value="/bin/systemctl"),
        patch("platforms.linux.services.subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 5)),
    ):
        assert LinuxServices().is_running("x") is False


def test_linux_and_stub_never_start_services():
    assert LinuxServices().start("x") is False
    assert STUB.services.start("x") is False
    assert STUB.services.is_running("x") is False


# --- capabilities -----------------------------------------------------------

def test_capabilities_by_backend():
    from platforms.linux import PLATFORM as LINUX
    from platforms.windows import PLATFORM as WINDOWS

    assert WINDOWS.has(Cap.BFE) is True
    assert LINUX.has(Cap.BFE) is False
    assert STUB.has(Cap.BFE) is False


def test_system_state_bfe_is_unknown_not_stopped_without_the_capability():
    from diagnostics import system_state

    other = Platform(name="x", messages=MagicMock(), privileges=MagicMock(), single_instance=MagicMock(),
                     services=MagicMock())
    with patch("platforms.current", return_value=other):
        with pytest.raises(LookupError):
            system_state._read_bfe()
    other.services.is_running.assert_not_called()


def test_startup_checks_skip_bfe_when_the_platform_has_no_such_service():
    from main import post_startup_check_workers as workers

    no_bfe = Platform(name="x", messages=MagicMock(), privileges=MagicMock(), single_instance=MagicMock(),
                      services=MagicMock())
    with (
        patch("platforms.current", return_value=no_bfe),
        patch("startup.bfe_util.ensure_bfe_running") as ensure,
        patch("startup.bfe_util.preload_service_status") as preload,
        patch("startup.check_start.collect_startup_notifications", return_value=[]),
        patch("startup.check_start.check_goodbyedpi", return_value=(False, "")),
        patch("startup.check_start.check_mitmproxy", return_value=(False, "")),
    ):
        try:
            workers.collect_startup_checks_payload(verbose_logging_enabled=False)
        except Exception:
            pass  # later steps of the payload may need a running app; only the BFE gate matters here
    ensure.assert_not_called()
    preload.assert_not_called()


def test_startup_checks_run_bfe_when_the_platform_has_it():
    from main import post_startup_check_workers as workers

    with_bfe = Platform(name="x", messages=MagicMock(), privileges=MagicMock(), single_instance=MagicMock(),
                        services=MagicMock(), capabilities=frozenset({Cap.BFE}))
    with (
        patch("platforms.current", return_value=with_bfe),
        patch("startup.bfe_util.ensure_bfe_running", return_value=(True, None)) as ensure,
        patch("startup.bfe_util.preload_service_status") as preload,
        patch("startup.check_start.collect_startup_notifications", return_value=[]),
        patch("startup.check_start.check_goodbyedpi", return_value=(False, "")),
        patch("startup.check_start.check_mitmproxy", return_value=(False, "")),
    ):
        try:
            workers.collect_startup_checks_payload(verbose_logging_enabled=False)
        except Exception:
            pass
    preload.assert_called_once_with("BFE")
    ensure.assert_called_once_with()
