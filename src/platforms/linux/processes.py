from __future__ import annotations

import os
import signal
import time

from platforms.base import ProcessSnapshotError


class LinuxProcesses:
    """Processes through psutil (already a runtime dependency) and /proc."""

    def hidden_process_options(self) -> dict:
        return {"start_new_session": True}

    def list_processes(self) -> list[tuple[int, str]]:
        try:
            return self.list_processes_strict()
        except ProcessSnapshotError:
            return []

    def list_processes_strict(self) -> list[tuple[int, str]]:
        try:
            import psutil

            records: list[tuple[int, str]] = []
            for proc in psutil.process_iter(["pid", "name"]):
                name = str(proc.info.get("name") or "").strip()
                if name:
                    records.append((int(proc.info["pid"]), name))
            return records
        except Exception as exc:  # psutil failing to enumerate is a refusal, not "no processes"
            raise ProcessSnapshotError(f"process list failed: {exc}") from exc

    def command_line(self, pid: int) -> str:
        try:
            with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            return ""
        return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)

    def exe_path(self, pid: int) -> str:
        try:
            return os.readlink(f"/proc/{int(pid)}/exe")
        except OSError:
            return ""

    def module_paths(self, pid: int, *, max_snapshot_attempts: int = 3) -> list[str]:
        """Files mapped into the process (shared objects and the program itself)."""
        paths: list[str] = []
        try:
            with open(f"/proc/{int(pid)}/maps", "rb") as handle:
                for line in handle.read().decode("utf-8", "replace").splitlines():
                    parts = line.split(None, 5)
                    if len(parts) == 6 and parts[5].startswith("/") and parts[5] not in paths:
                        paths.append(parts[5])
        except OSError:
            return []
        return paths

    def start_time(self, pid: int) -> float:
        try:
            import psutil

            return float(psutil.Process(int(pid)).create_time())
        except Exception:
            return 0.0

    def kill(self, pid: int, wait_timeout_ms: int = 3000) -> bool:
        """SIGTERM first, SIGKILL after half of the time; True when the process is gone."""
        pid = int(pid)
        try:
            import psutil

            proc = psutil.Process(pid)
        except Exception:
            return False
        deadline = time.monotonic() + max(wait_timeout_ms, 0) / 1000.0
        for sig, share in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 1.0)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            limit = time.monotonic() + max(deadline - time.monotonic(), 0) * share
            try:
                proc.wait(timeout=max(limit - time.monotonic(), 0.05))
                return True
            except Exception:
                if not proc.is_running() or proc.status() == "zombie":
                    return True
        return False
