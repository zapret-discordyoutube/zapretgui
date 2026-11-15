"""The Linux strategy runner: what the program drives instead of ``winws2.exe``.

It reads the preset (the preset text is the source of truth), turns the preset's relative file names into absolute
ones for the two Linux trees (read-only program folder, writable user folders), checks early what Linux cannot do,
and hands the argument list to the root helper, which installs the nftables table and starts ``nfqws2``.
The method names are those of ``winws_runtime.runners.runner_base.StrategyRunnerBase`` that the rest of the
program calls.
"""

from __future__ import annotations

import os
import re
import threading
from types import SimpleNamespace

from platforms.linux import capture_rules
from platforms.linux.helper.client import HelperClient, HelperUnavailable

# Where the first folder of a relative preset path really is, per layout (``ApplicationPaths`` attribute names).
_AT_PATH = re.compile(r"@([^:,\s]+)")
_PLAIN_FILE_OPTIONS = frozenset({"--hostlist", "--hostlist-exclude", "--hostlist-auto", "--ipset", "--ipset-exclude"})


def resolve_relative(path: str, paths) -> str | None:
    """The absolute place of a preset-relative file (``bin/x.bin``, ``lists/user/a.txt``); None: not ours to rewrite."""
    clean = path.replace("\\", "/").lstrip("/") if not os.path.isabs(path) else ""
    head, _, rest = clean.partition("/")
    if not rest:
        return None
    if head == "bin":
        return str(paths.bin_dir / rest)
    if head == "lua":
        return str(paths.lua_dir / rest)
    if head == "user":
        return str(paths.user_dir / rest)
    if head == "lists":
        second, _, tail = rest.partition("/")
        if second == "base" and tail:
            return str(paths.lists_base_dir / tail)
        return str(paths.lists_dir / rest)
    return None


def absolute_engine_args(args: list[str], paths) -> list[str]:
    """The preset arguments with their relative file names replaced by absolute ones. Capture options stay as written."""
    result: list[str] = []
    for arg in args:
        name, separator, value = arg.partition("=")
        if not separator or name.startswith("--wf-"):
            result.append(arg)
            continue

        def replace_at(match: re.Match) -> str:
            resolved = resolve_relative(match.group(1), paths)
            return "@" + (resolved if resolved is not None else match.group(1))

        new_value = _AT_PATH.sub(replace_at, value)
        if name in _PLAIN_FILE_OPTIONS and not value.startswith("@"):
            resolved = resolve_relative(value, paths)
            if resolved is not None:
                new_value = resolved
        result.append(f"{name}={new_value}")
    return result


class LinuxStrategyRunner:
    def __init__(self, engine_path: str, *, client: HelperClient | None = None, paths=None) -> None:
        self.winws_exe = str(engine_path)
        self._client = client or HelperClient()
        self._paths = paths
        self._lock = threading.RLock()
        self.running_process = None
        self.current_launch_label: str | None = None
        self.current_strategy_args: list[str] | None = None
        self.last_error_message = ""
        self._pid: int | None = None
        self._callbacks: dict[str, object] = {}

    # -- wiring -----------------------------------------------------------------------------------

    def application_paths(self):
        if self._paths is None:
            from config.runtime_layout import APPLICATION_PATHS

            self._paths = APPLICATION_PATHS
        return self._paths

    def configure_runtime_callbacks(self, **callbacks) -> None:
        self._callbacks = {key: value for key, value in callbacks.items() if value is not None}

    def launch_transition_in_progress(self, launch_method: str) -> bool:
        callback = self._callbacks.get("transition_in_progress")
        return bool(callback(launch_method)) if callable(callback) else False

    def _fail(self, message: str) -> bool:
        self.last_error_message = message
        try:
            from log.log import log

            log(f"Запуск движка на Linux не удался: {message}", "❌ ERROR")
        except Exception:
            pass
        for key in ("launch_error", "runner_failure"):
            callback = self._callbacks.get(key)
            if callable(callback):
                try:
                    callback(message) if key == "launch_error" else callback(launch_method="linux", error=message)
                except TypeError:
                    pass
                except Exception:
                    pass
                break
        return False

    # -- preset -----------------------------------------------------------------------------------

    def _read_arguments(self, preset_path: str) -> list[str]:
        from utils.atomic_text import read_preset_file_text
        from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

        return launch_args_from_preset_text(read_preset_file_text(preset_path))

    def _read_raw(self, name: str) -> str:
        root = self.application_paths().root
        path = os.path.realpath(root / name)
        if not path.startswith(os.path.realpath(root) + os.sep):
            raise OSError("the filter file is outside the program folder")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def validate_preset_file(self, preset_path: str) -> tuple[bool, str]:
        """Early check without touching the system: what Linux cannot honour, before a running engine is stopped."""
        try:
            args = self._read_arguments(preset_path)
        except Exception as exc:
            return False, f"Не удалось прочитать пресет: {exc}"
        translation = capture_rules.translate(args, queue_num=200, read_raw=self._read_raw)
        if not translation.ok:
            lines = ["Пресет содержит параметры, которые не поддерживаются в Linux:", *translation.unsupported]
            return False, "\n".join(lines)
        if not translation.ruleset:
            return False, "В пресете нет параметров перехвата (--wf-*): программе нечего направлять движку"
        return True, ""

    # -- the engine -------------------------------------------------------------------------------

    def start_from_preset_file(self, preset_path: str, strategy_name: str = "Preset", **_kwargs) -> bool:
        ok, report = self.validate_preset_file(preset_path)
        if not ok:
            return self._fail(report)
        try:
            args = absolute_engine_args(self._read_arguments(preset_path), self.application_paths())
            answer = self._client.start(args, label=strategy_name)
        except HelperUnavailable as exc:
            return self._fail(str(exc))
        except Exception as exc:
            return self._fail(f"Не удалось запустить: {exc}")
        if not answer.get("ok"):
            return self._fail(str(answer.get("error") or "the helper refused the start"))
        with self._lock:
            self._pid = answer.get("pid")
            self.current_launch_label = strategy_name
            self.current_strategy_args = args
            self.last_error_message = ""
        return True

    def switch_preset_file_fast(self, preset_path: str, strategy_name: str = "Preset", *, is_current=None) -> bool:
        if callable(is_current) and not is_current():
            return True  # a newer request replaced this one: nothing to do
        return self.start_from_preset_file(preset_path, strategy_name)

    def stop(self, *, cleanup_services: bool = True) -> bool:
        try:
            answer = self._client.stop()
        except HelperUnavailable:
            # no helper means no rules and no engine: nothing can be left running
            answer = {"ok": True}
        with self._lock:
            self._pid = None
            self.current_launch_label = None
        return bool(answer.get("ok"))

    def _status(self) -> dict:
        try:
            return self._client.status()
        except HelperUnavailable:
            return {"ok": True, "running": False}

    def is_running(self) -> bool:
        return bool(self._status().get("running"))

    def adopt_running_engine(self) -> bool:
        """The window was restarted while the helper kept the engine running."""
        status = self._status()
        if not status.get("running"):
            return False
        with self._lock:
            self._pid = status.get("pid")
            self.current_launch_label = status.get("label") or self.current_launch_label
        return True

    def stop_extra_adopted_engines(self) -> None:
        pass  # the helper runs one engine at a time

    def stop_background_watchers(self) -> None:
        pass

    def get_current_strategy_info(self) -> dict:
        return {"name": self.current_launch_label, "args": list(self.current_strategy_args or [])}

    def get_process(self):
        return None

    def get_runner_state_snapshot(self):
        return SimpleNamespace(pid=self._pid if isinstance(self._pid, int) else None)

    def read_post_mortem_output(self) -> str:
        try:
            with open("/run/zapretgui/engine.log", encoding="utf-8", errors="replace") as handle:
                return handle.read()[-20000:]
        except OSError:
            return ""

    def build_post_mortem_snapshot(self):
        return None
