"""The socket loop of the helper. ``python -m platforms.linux.helper`` is what the systemd unit runs (as root)."""

from __future__ import annotations

import grp
import os
import pwd
import signal
import socket
import struct
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from platforms.linux.helper.protocol import (
    SOCKET_GROUP,
    SOCKET_PATH,
    ProtocolError,
    answer_error,
    encode,
    read_message,
)
from platforms.linux.helper.service import Caller, HelperService

WATCH_INTERVAL_SECONDS = 1.0
CLIENT_TIMEOUT_SECONDS = 20.0


def peer_credentials(conn: socket.socket) -> tuple[int, int, int]:
    """(uid, gid, pid) of the process on the other end, from the kernel (cannot be faked by the message)."""
    pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid, gid, pid


def authorize_by_group(conn: socket.socket, group_name: str = SOCKET_GROUP) -> Caller | None:
    """root, or a member of the helper group (named in the group or having it as the main group)."""
    uid, gid, _pid = peer_credentials(conn)
    try:
        entry = pwd.getpwuid(uid)
    except KeyError:
        return None
    if uid == 0:
        return Caller(uid=0, home=entry.pw_dir, gid=0)
    try:
        group = grp.getgrnam(group_name)
    except KeyError:
        return None
    if entry.pw_name in group.gr_mem or entry.pw_gid == group.gr_gid or gid == group.gr_gid:
        return Caller(uid=uid, home=entry.pw_dir, gid=entry.pw_gid)
    return None


def _handle_connection(conn: socket.socket, service: HelperService, authorize: Callable[[socket.socket], Caller | None]) -> None:
    with conn:
        conn.settimeout(CLIENT_TIMEOUT_SECONDS)
        try:
            caller = authorize(conn)
            if caller is None:
                conn.sendall(encode(answer_error("not allowed: the user is not in the group %s" % SOCKET_GROUP)))
                return
            request = read_message(conn)
            if request is None:
                return
            conn.sendall(encode(service.handle(request, caller)))
        except ProtocolError as exc:
            _try_send(conn, answer_error(f"bad request: {exc}"))
        except (OSError, socket.timeout):
            pass


def _try_send(conn: socket.socket, message: dict) -> None:
    try:
        conn.sendall(encode(message))
    except OSError:
        pass


def open_socket(path: str = SOCKET_PATH, *, group_name: str | None = SOCKET_GROUP, mode: int = 0o660) -> socket.socket:
    directory = Path(path).parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o755)  # systemd makes /run/zapretgui this way (RuntimeDirectoryMode); an init script may have another umask
    except OSError:
        pass
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    if group_name:
        try:
            os.chown(path, 0, grp.getgrnam(group_name).gr_gid)
        except (KeyError, PermissionError):
            pass
    os.chmod(path, mode)
    server.listen(8)
    server.settimeout(1.0)
    return server


def serve(
    service: HelperService,
    *,
    path: str = SOCKET_PATH,
    authorize: Callable[[socket.socket], Caller | None] = authorize_by_group,
    stop_event: threading.Event | None = None,
    server: socket.socket | None = None,
) -> None:
    stop = stop_event or threading.Event()
    service.cleanup_stale()  # whatever a crashed predecessor left behind goes first
    listener = server or open_socket(path)

    def watch() -> None:
        while not stop.wait(WATCH_INTERVAL_SECONDS):
            service.check_engine_alive()

    watcher = threading.Thread(target=watch, name="engine-watch", daemon=True)
    watcher.start()
    try:
        while not stop.is_set():
            try:
                conn, _addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=_handle_connection, args=(conn, service, authorize), daemon=True).start()
    finally:
        stop.set()
        service.stop()  # leaving without leaving the firewall rules behind
        try:
            listener.close()
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    if os.geteuid() != 0:
        print("zapretgui-helper must run as root (the init system starts it: systemd, OpenRC or sysvinit)", file=sys.stderr)
        return 1
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_args: stop.set())
    # For development machines: a prepared folder instead of /opt/zapretgui (packaging/linux/README.md).
    program_root = os.environ.get("ZAPRET_HELPER_PROGRAM_ROOT")
    service = HelperService(program_root=program_root, engine_path=Path(program_root) / "bin" / "nfqws2") if program_root else HelperService()
    serve(service, stop_event=stop)
    return 0
