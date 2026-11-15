"""The lifecycle of the bypass on Linux: start, stop, status, and removal of leftovers.

Safety rules (the internet must never be cut by a crash):

* the firewall table ``inet zapretgui`` is the only thing this service creates; it is removed on stop, at the
  start of the next run, when the engine dies unexpectedly and whenever ``cleanup_stale`` runs (service start);
* the engine opens its queue first, the rules that send packets to it are installed second, and removed first
  on the way out; the rules use ``queue ... bypass`` so packets pass while nobody listens;
* a request is refused (and nothing is changed) when the translation reports options it cannot honour.

Every system call goes through ``SystemCalls`` so the logic is tested without root.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from platforms.linux import capture_rules
from platforms.linux.helper import policy
from platforms.linux.helper.protocol import answer_error, answer_ok

DEFAULT_QUEUE = 200
STATE_PATH = "/run/zapretgui/state.json"
ENGINE_LOG_PATH = "/run/zapretgui/engine.log"
NOBODY_ID = 65534  # the engine of a root caller runs as nobody
START_CHECK_SECONDS = 0.6
STOP_GRACE_SECONDS = 3.0


class EngineProcess:
    """What the service needs from a started engine (``subprocess.Popen`` has all of it)."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass
class SystemCalls:
    nft: Callable[[str], tuple[int, str]]
    read_sysctl: Callable[[str], str | None]
    write_sysctl: Callable[[str, str], bool]
    spawn: Callable[[list[str]], EngineProcess]
    pid_is_engine: Callable[[int, str], bool]
    kill_pid: Callable[[int, int], None]
    pid_alive: Callable[[int], bool]
    sleep: Callable[[float], None] = time.sleep


def real_system_calls() -> SystemCalls:
    def nft(script: str) -> tuple[int, str]:
        try:
            done = subprocess.run(["nft", "-f", "-"], input=script, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)
        return done.returncode, (done.stderr or done.stdout).strip()

    def read_sysctl(key: str) -> str | None:
        try:
            return Path("/proc/sys", *key.split(".")).read_text().strip()
        except OSError:
            return None

    def write_sysctl(key: str, value: str) -> bool:
        try:
            Path("/proc/sys", *key.split(".")).write_text(value)
            return True
        except OSError:
            return False

    def spawn(command: list[str]) -> EngineProcess:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def pid_is_engine(pid: int, engine_path: str) -> bool:
        try:
            return os.path.realpath(f"/proc/{pid}/exe") == os.path.realpath(engine_path)
        except OSError:
            return False

    def kill_pid(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except OSError:
            pass

    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    return SystemCalls(nft, read_sysctl, write_sysctl, spawn, pid_is_engine, kill_pid, pid_alive)


@dataclass
class Caller:
    """Who asks (taken from the socket, never from the message)."""

    uid: int
    home: str | None
    gid: int = NOBODY_ID


@dataclass
class _Running:
    process: EngineProcess
    started: float
    label: str
    old_sysctls: dict[str, str | None] = field(default_factory=dict)


class HelperService:
    def __init__(
        self,
        system: SystemCalls | None = None,
        *,
        state_path: str = STATE_PATH,
        engine_path: Path | str = policy.ENGINE_PATH,
        program_root: Path | str = policy.PROGRAM_ROOT,
        queue_num: int = DEFAULT_QUEUE,
        log_path: str = ENGINE_LOG_PATH,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._system = system or real_system_calls()
        self._state_path = state_path
        self._engine_path = str(engine_path)
        self._program_root = Path(program_root)
        self._queue = int(queue_num)
        self._log_path = log_path
        self._clock = clock
        self._lock = threading.RLock()
        self._running: _Running | None = None

    # -- requests ---------------------------------------------------------------------------------

    def handle(self, request: dict, caller: Caller) -> dict:
        op = request.get("op")
        try:
            if op == "start":
                return self.start(request.get("args"), caller, label=str(request.get("label") or ""))
            if op == "stop":
                return self.stop()
            if op == "status":
                return self.status()
            if op == "cleanup":
                return self.cleanup_stale()
        except Exception as exc:  # the service must never die from one bad request
            return answer_error(f"internal error: {type(exc).__name__}: {exc}")
        return answer_error(f"unknown operation {op!r}")

    def start(self, args, caller: Caller, *, label: str = "") -> dict:
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            return answer_error("args must be a list of strings")
        problems = policy.check_args(args, policy.allowed_roots_for(caller.home if caller.uid != 0 else None, self._program_root))
        if problems:
            return answer_error("refused: " + "; ".join(problems[:5]))
        translation = capture_rules.translate(args, queue_num=self._queue, read_raw=self._read_raw)
        if not translation.ok:
            return answer_error("not supported on Linux: " + "; ".join(translation.unsupported))
        if not translation.ruleset:
            return answer_error("the preset captures no traffic (no --wf-* options)")
        if not os.access(self._engine_path, os.X_OK):
            return answer_error(f"the engine is not installed: {self._engine_path}")

        with self._lock:
            self._stop_locked()  # a previous run is replaced, never stacked
            self._remove_table()
            old = self._apply_sysctls(translation.sysctls)
            # The engine opens its queue with the rights it starts with, then gives them up: it continues as the
            # person who asked (so it reads their presets and lists, and never has more rights than they do).
            uid, gid = (caller.uid, caller.gid) if caller.uid != 0 else (NOBODY_ID, NOBODY_ID)
            command = [
                self._engine_path,
                f"--qnum={self._queue}",
                f"--uid={uid}:{gid}",
                f"--debug=@{self._log_path}",
                *translation.engine_args,
            ]
            try:
                process = self._system.spawn(command)
            except OSError as exc:
                self._restore_sysctls(old)
                return answer_error(f"cannot start the engine: {exc}")
            self._system.sleep(START_CHECK_SECONDS)
            code = process.poll()
            if code is not None:
                self._restore_sysctls(old)
                return answer_error(f"the engine exited at once with code {code} (see {self._log_path})")
            rc, output = self._system.nft(translation.ruleset)
            if rc != 0:
                self._terminate(process)
                self._restore_sysctls(old)
                return answer_error(f"cannot install the firewall rules: {output or rc}")
            self._running = _Running(process, self._clock(), label, old)
            self._write_state(process.pid, old)
            return answer_ok(pid=process.pid, ignored=list(translation.ignored))

    def stop(self) -> dict:
        with self._lock:
            was_running = self._running is not None
            self._stop_locked()
            self._remove_table()
            return answer_ok(was_running=was_running)

    def status(self) -> dict:
        with self._lock:
            running = self._running
            if running is not None and running.process.poll() is not None:
                code = running.process.poll()
                self._stop_locked()
                self._remove_table()
                return answer_ok(running=False, exited_with=code)
            if running is None:
                return answer_ok(running=False)
            return answer_ok(running=True, pid=running.process.pid, since=running.started, label=running.label)

    def check_engine_alive(self) -> bool:
        """Called by the server's watcher: when the engine died, the rules go at once. True: nothing to do."""
        with self._lock:
            if self._running is None or self._running.process.poll() is None:
                return True
            self._stop_locked()
            self._remove_table()
            return False

    def cleanup_stale(self) -> dict:
        """After a crash or at service start: remove our table, kill a leftover engine of ours, restore sysctls."""
        with self._lock:
            self._remove_table()
            removed_pid = None
            old: dict[str, str | None] = {}
            try:
                state = json.loads(Path(self._state_path).read_text(encoding="utf-8"))
                pid = int(state.get("pid") or 0)
                old = dict(state.get("sysctls") or {})
            except (OSError, ValueError, TypeError):
                pid = 0
            if pid and self._system.pid_alive(pid) and self._system.pid_is_engine(pid, self._engine_path):
                self._system.kill_pid(pid, signal.SIGTERM)
                self._system.sleep(0.2)
                if self._system.pid_alive(pid):
                    self._system.kill_pid(pid, signal.SIGKILL)
                removed_pid = pid
            self._restore_sysctls(old)
            self._forget_state()
            return answer_ok(removed_pid=removed_pid)

    # -- parts -------------------------------------------------------------------------------------

    def _read_raw(self, name: str) -> str:
        path = Path(os.path.realpath(self._program_root / name))
        root = Path(os.path.realpath(self._program_root))
        if root not in path.parents:
            raise OSError("the filter file is outside the program folder")
        return path.read_text(encoding="utf-8")

    def _remove_table(self) -> None:
        self._system.nft(capture_rules.teardown_ruleset())

    def _apply_sysctls(self, sysctls) -> dict[str, str | None]:
        old: dict[str, str | None] = {}
        for key, value in sysctls:
            old[key] = self._system.read_sysctl(key)
            self._system.write_sysctl(key, value)
        return old

    def _restore_sysctls(self, old: dict[str, str | None]) -> None:
        for key, value in old.items():
            if value is not None:
                self._system.write_sysctl(key, value)

    def _stop_locked(self) -> None:
        running, self._running = self._running, None
        if running is None:
            return
        # rules first: after this the traffic no longer goes to the engine, so killing it cuts nothing
        self._remove_table()
        self._terminate(running.process)
        self._restore_sysctls(running.old_sysctls)
        self._forget_state()

    def _terminate(self, process: EngineProcess) -> None:
        try:
            process.terminate()
            process.wait(timeout=STOP_GRACE_SECONDS)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=STOP_GRACE_SECONDS)
            except Exception:
                pass

    def _write_state(self, pid: int, old: dict[str, str | None]) -> None:
        try:
            path = Path(self._state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"pid": pid, "table": capture_rules.TABLE_NAME, "sysctls": old}), encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            pass

    def _forget_state(self) -> None:
        try:
            os.unlink(self._state_path)
        except OSError:
            pass
