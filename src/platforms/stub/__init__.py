"""Backend for systems the program does not support: safe answers, no windows."""

from __future__ import annotations

import threading
from typing import Callable, Sequence

from platforms.netprobe_types import HOP_UNSUPPORTED, DnsAnswer, PingResult, TraceHopResult
from platforms.base import (
    GuiAutostartResult,
    InstanceLock,
    MessageKind,
    NoWorkingSet,
    Platform,
    ProcessSnapshotError,
    SystemSupport,
    print_to_stderr,
)


class _StderrMessageBox:
    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        print_to_stderr(title, message)


class _NoPrivileges:
    gui_needs_elevation = False

    def is_elevated(self) -> bool:
        return False

    def relaunch_elevated(self, executable: str, args: Sequence[str]) -> bool:
        return False


class _NoSingleInstance:
    def acquire(self, name: str) -> InstanceLock:
        return InstanceLock(already_running=False)

    def create_show_signal(self) -> bool:
        return False

    def signal_running_instance(self) -> bool:
        return False

    def start_show_watcher(
        self, callback: Callable[[], None], *, wait_fn: Callable[[], int] | None = None
    ) -> threading.Thread | None:
        return None


class _NoServices:
    def is_running(self, name: str) -> bool:
        return False

    def start(self, name: str, timeout: float = 10) -> bool:
        return False


class _NoProcesses:
    def list_processes(self) -> list[tuple[int, str]]:
        return []

    def list_processes_strict(self) -> list[tuple[int, str]]:
        raise ProcessSnapshotError("no process access on this system")

    def command_line(self, pid: int) -> str:
        return ""

    def exe_path(self, pid: int) -> str:
        return ""

    def module_paths(self, pid: int, *, max_snapshot_attempts: int = 3) -> list[str]:
        return []

    def start_time(self, pid: int) -> float:
        return 0.0

    def kill(self, pid: int, wait_timeout_ms: int = 3000) -> bool:
        return False


class _NoNetProbe:
    def ping_ipv4_host(self, host, *, count, timeout_ms, resolved_ip=None, cancelled=None) -> PingResult:
        return PingResult(ok=False, sent=max(0, int(count)), error_code="UNSUPPORTED", detail="no network probes here")

    def ping_ipv6(self, ip, *, count, timeout_ms, cancelled=None) -> PingResult:
        return PingResult(ok=False, sent=max(0, int(count)), error_code="UNSUPPORTED", detail="no network probes here")

    def trace_hop_ipv4(self, ip, ttl, *, timeout_ms) -> TraceHopResult:
        return TraceHopResult(HOP_UNSUPPORTED)

    def query_ipv4(self, name, *, timeout, cancelled=None) -> DnsAnswer:
        return DnsAnswer(status=-1, detail="no DNS probe on this system")

    def system_dns_servers(self) -> tuple[str, ...]:
        return ()

    def tcp_server_window(self, ip, *, port=443, timeout=4.0) -> int | None:
        return None

    def hosts_file_path(self) -> str:
        return ""


class _NoAutostart:
    def enable(self, executable: str, *, status_cb=None) -> GuiAutostartResult:
        return GuiAutostartResult(success=False, message="autostart is not available on this system")

    def disable(self) -> GuiAutostartResult:
        return GuiAutostartResult(success=True)

    def ensure_current(self, executable: str) -> bool:
        return False


class _NoDesktop:
    def user_idle_ms(self) -> int | None:
        return None

    def uses_qt_file_drop(self) -> bool:
        return True

    def bind_window(self, window) -> bool:
        return False

    def dropped_file_paths(self, message):
        return None

    def create_drag_hover_detector(self, window, *, on_hover_start, on_hover_end):
        return None

    def handle_native_event(self, window, message) -> bool:
        return False

    def cloak_until_painted(self, window):
        return None

    def install_theme_watcher(self, app, window):
        return None


class _NoLaunch:
    def process_creation_unix_seconds(self) -> float | None:
        return None

    def system_support(self) -> SystemSupport:
        return SystemSupport(supported=True)

    def source_launch_message(self) -> str:
        return "Запуск приложения из исходников запрещён."


PLATFORM = Platform(
    name="stub",
    messages=_StderrMessageBox(),
    privileges=_NoPrivileges(),
    single_instance=_NoSingleInstance(),
    services=_NoServices(),
    launch=_NoLaunch(),
    processes=_NoProcesses(),
    netprobe=_NoNetProbe(),
    autostart=_NoAutostart(),
    desktop=_NoDesktop(),
    working_set=NoWorkingSet(),
)
