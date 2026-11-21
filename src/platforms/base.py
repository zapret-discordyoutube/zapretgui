"""The shared shape of a platform backend.

One ``Platform`` object per running system. The rest of the program asks it for
what it needs (``platform.messages.show(...)``) and never checks "am I on
Windows?" itself. Every new interface of the multi-platform project is a new
field here, plus one implementation per backend (windows, linux, stub).
Plan and file-to-interface map: ``app/PLATFORM_MIGRATION_MAP.md``.

This module must stay tiny and import nothing heavy: the launch gate uses it
before Qt, the logger or any other part of the program exists.
"""

from __future__ import annotations

import enum
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol, Sequence

if TYPE_CHECKING:
    from platforms.dns_types import InternetRoute, RawInterface, StaticDns
    from platforms.netprobe_types import DnsAnswer, PingResult, TraceHopResult


class MessageKind(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MessageBox(Protocol):
    """A blocking message for the person at the screen, usable before Qt starts."""

    def show(self, title: str, message: str, kind: MessageKind = MessageKind.ERROR) -> None:
        """Shows the message and returns after it is closed. Never raises."""


class Privileges(Protocol):
    """Administrator / root rights of the running program."""

    @property
    def gui_needs_elevation(self) -> bool:
        """True when the window itself must run elevated (Windows: the engine needs it).

        On Linux the window runs as a normal user; a root helper service does the
        privileged work, so the program never asks to restart itself as root.
        """

    def is_elevated(self) -> bool:
        """True when the process runs as administrator / root. Never raises."""

    def relaunch_elevated(self, executable: str, args: Sequence[str]) -> bool:
        """Asks the system to start ``executable args`` with higher rights.

        True when the request was accepted (the caller then exits); False when it
        was refused or is not possible on this system.
        """


class Services(Protocol):
    """The system's background services (Windows services, systemd units)."""

    def is_running(self, name: str) -> bool:
        """True when the service exists and runs. A service that does not exist is "not running"."""

    def start(self, name: str, timeout: float = 10) -> bool:
        """Tries to start the service and waits up to ``timeout`` seconds. True when it runs afterwards."""


@dataclass(frozen=True, slots=True)
class SystemSupport:
    """Whether the running system is new enough for the program."""

    supported: bool
    os_name: str = ""
    title: str = ""  # title of the window that tells the person (empty when supported)
    message: str = ""  # the text of that window, shown as is


class Launch(Protocol):
    """What the very first start needs to know about the system. Used before Qt and the logger exist."""

    def process_creation_unix_seconds(self) -> float | None:
        """When the operating system created this process (Unix seconds); None when unknown. Never raises."""

    def system_support(self) -> SystemSupport:
        """Whether this version of the system is supported. Never raises; an unknown version counts as supported."""

    def source_launch_message(self) -> str:
        """Why starting from source is refused and what to do instead, for the person who tried."""

    def prepare_process(self) -> None:
        """Set the process up before the program imports anything of its own. May replace the process
        (Linux restarts it once in UTF-8 mode when the locale is not UTF-8). Does nothing when nothing is needed."""


class ProcessSnapshotError(OSError):
    """The system refused to list the processes. A refusal, not "there are no processes"."""


class Processes(Protocol):
    """Looking at, measuring and stopping other processes."""

    def hidden_process_options(self) -> dict:
        """Extra ``subprocess.Popen`` arguments that start a helper program without a window of its own and, where the
        system has process groups, as the leader of one (so the whole group can be stopped)."""

    def list_processes(self) -> list[tuple[int, str]]:
        """``(pid, executable name)`` of every process. Lenient: an empty list when the system refuses."""

    def list_processes_strict(self) -> list[tuple[int, str]]:
        """Same list, but raises ``ProcessSnapshotError`` on a refusal.

        For decisions like "nothing left to stop": an empty list caused by a failure must not be taken for
        "no processes".
        """

    def command_line(self, pid: int) -> str:
        """The command line of a process; empty when it cannot be read."""

    def exe_path(self, pid: int) -> str:
        """Full path of the executable of a process; empty when it cannot be read."""

    def module_paths(self, pid: int, *, max_snapshot_attempts: int = 3) -> list[str]:
        """Paths of the files a process has loaded (DLLs / shared objects); empty when unknown."""

    def start_time(self, pid: int) -> float:
        """When the process was created (Unix seconds); 0.0 when unknown."""

    def kill(self, pid: int, wait_timeout_ms: int = 3000) -> bool:
        """Ends a process and waits for its exit. True only when the exit is confirmed."""


@dataclass(frozen=True, slots=True)
class WorkingSetSnapshot:
    """Address ranges (start, length in bytes) that were in the program's memory."""

    ranges: tuple[tuple[int, int], ...]
    total_bytes: int
    taken_ms: float


@dataclass(frozen=True, slots=True)
class PrefetchResult:
    ranges: int
    total_bytes: int
    elapsed_ms: float
    ok: bool


@dataclass(frozen=True, slots=True)
class WorkingSetHold:
    """Result of trying to hold the working set: how much was asked and how it ended."""

    bytes: int
    ok: bool
    error: int


class WorkingSet(Protocol):
    """Keeping the program's memory resident while its window is hidden (Windows pages memory out).

    Only meaningful where ``Cap.WORKING_SET`` is present; elsewhere every call answers None / False.
    """

    def snapshot_working_set(self) -> WorkingSetSnapshot | None: ...

    def prefetch_working_set(self, snapshot: WorkingSetSnapshot | None) -> PrefetchResult | None: ...

    def hold_working_set(self, size_bytes: int) -> WorkingSetHold | None: ...

    def release_working_set_hold(self) -> bool: ...

    def process_page_faults(self) -> int | None: ...

    def current_working_set_bytes(self) -> int | None: ...

    def available_physical_bytes(self) -> int | None: ...


class NoWorkingSet:
    """For systems that manage the memory of a hidden window well enough on their own."""

    def snapshot_working_set(self) -> WorkingSetSnapshot | None:
        return None

    def prefetch_working_set(self, snapshot: WorkingSetSnapshot | None) -> PrefetchResult | None:
        return None

    def hold_working_set(self, size_bytes: int) -> WorkingSetHold | None:
        return None

    def release_working_set_hold(self) -> bool:
        return False

    def process_page_faults(self) -> int | None:
        return None

    def current_working_set_bytes(self) -> int | None:
        return None

    def available_physical_bytes(self) -> int | None:
        return None


class NetProbe(Protocol):
    """Measurements of the network the way the system sees it. Results use ``platforms.netprobe_types``."""

    def ping_ipv4_host(
        self,
        host: str,
        *,
        count: int,
        timeout_ms: int,
        resolved_ip: str | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> "PingResult":
        """ICMP echo to an IPv4 host without starting a ``ping`` program. ``cancelled`` is polled before each packet."""

    def ping_ipv6(
        self, ip: str, *, count: int, timeout_ms: int, cancelled: Callable[[], bool] | None = None
    ) -> "PingResult":
        """The same for an IPv6 address."""

    def trace_hop_ipv4(self, ip: str, ttl: int, *, timeout_ms: int) -> "TraceHopResult":
        """One echo packet that expires after ``ttl`` hops: who answered (router, target, nobody, unsupported)."""

    def query_ipv4(
        self, name: str, *, timeout: float, cancelled: Callable[[], bool] | None = None
    ) -> "DnsAnswer":
        """A records from the system's DNS servers, bypassing the resolver cache and the hosts file."""

    def system_dns_servers(self) -> tuple[str, ...]:
        """IPv4 addresses of the DNS servers the system uses now."""

    def tcp_server_window(self, ip: str, *, port: int = 443, timeout: float = 4.0) -> int | None:
        """The server's receive window right after connecting, in bytes; None when unknown."""

    def hosts_file_path(self) -> str:
        """Where the system keeps its hosts file."""


@dataclass(frozen=True, slots=True)
class GuiAutostartResult:
    success: bool
    removed_count: int = 0
    restart_requested: bool = False
    message: str = ""


class GuiAutostart(Protocol):
    """Starting the program's window (in the tray) when the person logs in.

    Windows: a scheduler task (an elevated program cannot start from the Startup folder).
    Linux: an XDG autostart entry in the user's own ``~/.config/autostart``.
    """

    def enable(self, executable: str, *, status_cb: Callable[[str], None] | None = None) -> GuiAutostartResult:
        """Registers ``executable`` to start at login, replacing older registrations. Never raises."""

    def disable(self) -> GuiAutostartResult:
        """Removes every registration. ``success`` is False when something is still registered afterwards."""

    def ensure_current(self, executable: str) -> bool:
        """Repairs a registration that points elsewhere (moved program) or was made the old way.

        Call it only when the person wants autostart. True when something was changed.
        """


class Desktop(Protocol):
    """How the program's window meets the desktop: native messages, file drops, the system theme, idle time.

    Every method is safe to call on any system and answers "nothing to do" where the desktop has no such
    mechanism (None / False). Windows-only message handling lives in ``platforms/windows``.
    """

    def user_idle_ms(self) -> int | None:
        """Milliseconds since the last keyboard or mouse input; None when the system cannot say."""

    def uses_qt_file_drop(self) -> bool:
        """True when Qt's own drag-and-drop receives files (False: the system hands them over in a native message)."""

    def bind_window(self, window) -> bool:
        """Binds the window's native handle to system notifications (file drops, locked screen, display off).

        Returns whether native file drop is on. Call again when Qt recreates the native window.
        """

    def dropped_file_paths(self, message) -> list[str] | None:
        """Paths from a native file-drop message; None when the message is not a file drop."""

    def create_drag_hover_detector(self, window, *, on_hover_start: Callable[[], None], on_hover_end: Callable[[], None]):
        """An object with ``start()`` / ``stop()`` that reports files dragged over the window; None when not needed."""

    def handle_native_event(self, window, message) -> bool:
        """Looks at a native window message. True when it was fully handled and Qt must not process it."""

    def cloak_until_painted(self, window):
        """Hides a window that is being shown until its first frame is painted (no white flash); None when not needed."""

    def install_theme_watcher(self, app, window):
        """Follows the system light/dark setting; returns an object with ``cleanup()`` or None."""


class Tray(Protocol):
    """The icon in the notification area and its menu.

    ``create_manager`` returns an object the program drives: ``apply_launch_status``, ``show_notification``,
    ``show_window``, ``hide_to_tray``, ``hide_icon``, ``cleanup`` ... Where the desktop has no tray the
    manager is a quiet stand-in whose ``hide_to_tray`` answers False, so a window is never hidden with no
    way to bring it back.
    """

    def create_manager(self, *, window_port, icon_path: str, app_version: str, tray_feature):
        """Builds the tray manager for the main window."""


class SystemSettings(Protocol):
    """Read-only questions to the system's own settings (the registry on Windows, XDG tools on Linux).

    The program's own settings are not here: they live in ``settings.sqlite3`` on every system.
    """

    def accent_color(self) -> str | None:
        """The accent colour the person chose in the system as ``#rrggbb``; None when there is none."""

    def url_scheme_handler_registered(self, scheme: str) -> bool:
        """Whether some program is registered to open ``scheme://`` links (``tg`` for Telegram)."""

    def default_browser_id(self) -> str:
        """The system's name for the default browser (Windows: the ProgId; Linux: the .desktop file); empty when unknown."""

    def proxy_settings(self) -> tuple[str, str]:
        """``(proxy server, automatic-config URL)`` the system hands to programs; empty strings when none."""

    def browser_paths(self) -> list[str]:
        """Paths of the installed Chromium-family browsers that can load a page without a window (Edge first, then
        Chrome, then Chromium); one path per browser, empty when there is none."""


class DnsAdapters(Protocol):
    """Reading and changing the DNS servers of the network interfaces (Windows: IP Helper API; Linux: NetworkManager
    or systemd-resolved). ``guid`` is the backend's own interface name, see ``platforms.dns_types``.

    Every method that changes something raises ``DnsBackendError`` with a readable text when the system refuses.
    """

    def list_interfaces(self) -> list[RawInterface]:
        """The interfaces a person could mean (no hidden service interfaces). Lenient: an empty list when unknown."""

    def internet_route(self) -> InternetRoute:
        """Which interfaces the internet goes through right now."""

    def read_static_dns(self, guid: str) -> StaticDns:
        """The DNS servers set by hand on the interface (empty: automatic)."""

    def write_dns(self, guid: str, servers: Sequence[str], *, ipv6: bool, doh_templates: dict[str, str] | None = None) -> None:
        """Set the DNS servers of one family; an empty list gives back the automatic ones."""

    def flush_resolver_cache(self) -> bool:
        """Empty the system's DNS cache; False when it could not be done."""

    def is_doh_supported(self) -> bool:
        """Whether the system can encrypt DNS itself (DNS over HTTPS) for the servers written."""


class HostsFile(Protocol):
    """The system's hosts file (names written by hand, the program's managed block inside it).

    Every method takes the ``path`` the program works with (``default_path()`` unless a test says otherwise).
    ``write`` and ``can_write`` raise ``PermissionError`` with a readable text when the system refuses.
    """

    encoding: str

    def default_path(self) -> Path:
        """Where the hosts file is."""

    def is_read_only(self, path: Path) -> bool:
        """Whether the file carries a write ban of its own (the read-only attribute on Windows)."""

    def write(self, path: Path, data: bytes) -> None:
        """Replace the content of the file with ``data`` (already encoded as ``encoding``)."""

    def check_name(self, path: Path) -> tuple[bool, str | None]:
        """``(True, None)``, or ``(False, text)`` when a wrongly named copy (``HOSTS``) is in the way."""

    def can_write(self, path: Path) -> None:
        """Return when a write would be allowed; raise ``PermissionError`` (or ``FileNotFoundError``) otherwise."""

    def restore_access(self, path: Path) -> tuple[bool, str]:
        """Give the file its standard access rights back (a button on the hosts page): ``(success, text)``."""


class Updates(Protocol):
    """What a release of this program looks like on this system and how a downloaded one gets installed.

    ``platform_key`` names the system in ``all_versions.json`` (the Windows entry is the channel entry itself, every
    other system has its own sub-entry under that name). ``package_suffix`` picks the release file of this system out of
    a release's files (``.exe`` installer, ``.deb`` package).
    """

    platform_key: str
    package_suffix: str

    def install_package(self, path: Path) -> tuple[bool, str]:
        """Install a downloaded and verified package with the system's own tools; ``(success, text for the person)``.
        Only where ``Cap.PACKAGE_UPDATE`` says so: Windows replaces the program itself (``Cap.SELF_UPDATE``)."""


class Engine(Protocol):
    """The DPI engine: the object that starts, switches and stops the bypass process for a preset.

    ``runner`` returns the one object the program drives (``start_from_preset_file``,
    ``switch_preset_file_fast``, ``stop``, ``is_running``, ``get_runner_state_snapshot`` ...). It is created on
    first use for a given engine program and replaced when the engine program changes.
    """

    def runner(self, engine_exe_path: str):
        """The runner for ``engine_exe_path``; creates or replaces it when needed."""

    def current_runner(self):
        """The runner that exists now, or None. Never creates one."""

    def reset_runner(self) -> None:
        """Stops the runner (waits for it) and forgets it."""

    def invalidate_runner(self) -> None:
        """Forgets the runner without waiting (its background watchers are stopped)."""

    def running_pids(self) -> list[int]:
        """Process ids of the engine that the scan of the process list by program name cannot find. Windows: none
        (its engine is found by name and path). Linux: the engine the root helper runs for this person."""


class Cap:
    """Names of optional features. ``Platform.has(Cap.X)`` tells whether this system has the feature.

    Features that exist on one system only (Windows Defender, the BFE firewall service ...) are
    switched off by their capability instead of failing; the interface hides them.
    """

    BFE = "windows_bfe"  # Base Filtering Engine: the Windows service WinDivert depends on
    SELF_UPDATE = "self_update"  # the program replaces itself from a downloaded installer
    WINDOWS_STARTUP_CHECKS = "windows_startup_checks"  # advisories about Windows software and settings at start
    DEFENDER = "windows_defender"  # the program can switch Windows Defender off and on
    MAX_BLOCKER = "max_blocker"  # blocking the MAX messenger through Explorer policies
    ANTIVIRUS_PROBE = "antivirus_probe"  # looking for antivirus programs that disturb the driver (Kaspersky ...)
    NETWORK_RESET = "windows_network_reset"  # "reset the Windows network stack" quick action
    BROWSER_POLICY_SWITCH = "browser_policy_switch"  # turning ECH/QUIC off in Edge, Chrome (registry policy) and Firefox (profile)
    DIRECT_ENGINE_PROCESS = "direct_engine_process"  # the program starts the engine process itself (no root helper in between)
    PACKAGE_UPDATE = "package_update"  # a newer version is announced and installed through the system's package manager
    LOCAL_DNS_PROXY = "local_dns_proxy"  # the built-in encrypted DNS (dnscrypt-proxy as a service) can be installed
    LEGACY_ENGINE_MODES = "legacy_engine_modes"  # the launch modes Zapret 1 (winws.exe) and the Orchestra exist: only Windows has those engines
    WORKING_SET = "working_set"  # the system pages out a hidden window's memory and the program can hold it


WAIT_SIGNALLED = 0
"""What a ``wait_fn`` returns when the "show window" signal arrived. Any other value ends the watcher."""


@dataclass(slots=True)
class InstanceLock:
    """Result of ``SingleInstance.acquire``. Keep it until the program exits."""

    already_running: bool
    _release: Callable[[], None] = lambda: None

    def release(self) -> None:
        self._release()


class SingleInstance(Protocol):
    """Only one copy of the program runs; a second copy asks the first to show its window.

    Order at start: ``acquire`` -> (second copy: ``signal_running_instance`` and exit) ->
    first copy: ``create_show_signal`` before Qt starts, so a signal sent while the
    first copy is still loading is not lost -> later ``start_show_watcher``.
    """

    def acquire(self, name: str) -> InstanceLock:
        """Takes the single-instance lock. If the system refuses, returns a lock that is not "already running"."""

    def create_show_signal(self) -> bool:
        """First copy: makes the "show window" signal receivable. False if impossible."""

    def signal_running_instance(self) -> bool:
        """Second copy: asks the running copy to show its window. False when nobody received it."""

    def start_show_watcher(
        self,
        callback: Callable[[], None],
        *,
        wait_fn: Callable[[], int] | None = None,
    ) -> threading.Thread | None:
        """Starts a daemon thread calling ``callback()`` for every signal; None if the signal was never created.

        ``wait_fn`` is a test seam replacing the real wait.
        """


def run_show_watcher(callback: Callable[[], None], wait_fn: Callable[[], int]) -> threading.Thread:
    """The loop every backend shares: wait, call back, repeat until the wait fails."""

    def _watch() -> None:
        while wait_fn() == WAIT_SIGNALLED:
            callback()

    thread = threading.Thread(target=_watch, daemon=True, name="show-event-watcher")
    thread.start()
    return thread


@dataclass(frozen=True, slots=True)
class Platform:
    name: str
    messages: MessageBox
    privileges: Privileges
    single_instance: SingleInstance
    services: Services
    launch: Launch
    processes: Processes
    netprobe: NetProbe
    autostart: GuiAutostart
    desktop: Desktop
    tray: Tray
    system_settings: SystemSettings
    dns: DnsAdapters
    hosts: HostsFile
    updates: Updates
    engine: Engine
    working_set: WorkingSet
    capabilities: frozenset[str] = frozenset()

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


def print_to_stderr(title: str, message: str) -> None:
    """The last resort when there is no way to show a window."""
    import sys

    print(f"{title}\n\n{message}", file=sys.stderr)
