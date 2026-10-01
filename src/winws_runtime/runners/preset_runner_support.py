from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
import re



_INLINE_ARG_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=--)")


def _split_launch_line(raw_line: str) -> list[str]:
    """Split a preset line into one or more CLI arguments.

    Circular/source presets may store several `--...` arguments on one line to
    keep a single logical strategy together. `subprocess.Popen()` still expects
    every CLI argument as a separate list item, so we split only on whitespace
    that introduces the next `--` argument.
    """
    stripped = str(raw_line or "").strip()
    if not stripped:
        return []
    if not stripped.startswith("--"):
        return [stripped]
    return [part.strip() for part in _INLINE_ARG_SPLIT_RE.split(stripped) if part.strip()]


def launch_args_from_preset_text(content: str) -> list[str]:
    """Собирает argv из текста выбранного preset-файла.

    Ведущий BOM (U+FEFF от «UTF-8 с BOM» в Блокноте) — это часть кодировки
    файла, а не текста пресета: str.strip() его не убирает, и без явного
    снятия он прилипал бы к первой опции. Проверка пресета снимает его так же.
    """
    text = str(content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    args: list[str] = []
    # Только «\n», как у парсера: splitlines() делил бы ещё по \x0b/\x85/\u2028.
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        args.extend(_split_launch_line(stripped))
    return args

@dataclass(frozen=True)
class PreparedPresetArtifact:
    preset_path: str
    cache_key: tuple[object, ...] | None
    normalized_text: str
    launch_args: tuple[str, ...]
    validation_ok: bool
    validation_report: str


class PresetRunnerState(str, Enum):
    IDLE = "idle"
    STOPPING = "stopping"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"


@dataclass(frozen=True)
class PresetRunnerStateSnapshot:
    state: PresetRunnerState
    generation: int
    preset_path: str
    strategy_name: str
    pid: int | None
    error: str
    reason: str


class PresetRunnerStateMachine:
    _ALLOWED: dict[PresetRunnerState, set[PresetRunnerState]] = {
        PresetRunnerState.IDLE: {PresetRunnerState.STARTING, PresetRunnerState.FAILED},
        PresetRunnerState.STOPPING: {PresetRunnerState.IDLE, PresetRunnerState.STARTING, PresetRunnerState.FAILED},
        PresetRunnerState.STARTING: {PresetRunnerState.RUNNING, PresetRunnerState.FAILED, PresetRunnerState.IDLE},
        PresetRunnerState.RUNNING: {
            PresetRunnerState.STARTING,
            PresetRunnerState.STOPPING,
            PresetRunnerState.FAILED,
            PresetRunnerState.IDLE,
        },
        PresetRunnerState.FAILED: {PresetRunnerState.IDLE, PresetRunnerState.STARTING},
    }

    def __init__(self) -> None:
        self._generation = 0
        self._snapshot = PresetRunnerStateSnapshot(
            state=PresetRunnerState.IDLE,
            generation=0,
            preset_path="",
            strategy_name="",
            pid=None,
            error="",
            reason="initialized",
        )

    def snapshot(self) -> PresetRunnerStateSnapshot:
        return self._snapshot

    def transition(
        self,
        target: PresetRunnerState,
        *,
        preset_path: str = "",
        strategy_name: str = "",
        pid: int | None = None,
        error: str = "",
        reason: str = "",
        allow_same: bool = False,
    ) -> PresetRunnerStateSnapshot:
        current = self._snapshot.state
        if target == current and not allow_same:
            return self._snapshot

        allowed = self._ALLOWED.get(current, set())
        if target != current and target not in allowed:
            raise RuntimeError(f"Invalid preset runner state transition: {current.value} -> {target.value}")

        self._generation += 1
        self._snapshot = PresetRunnerStateSnapshot(
            state=target,
            generation=self._generation,
            preset_path=str(preset_path or ""),
            strategy_name=str(strategy_name or ""),
            pid=pid,
            error=str(error or ""),
            reason=str(reason or ""),
        )
        return self._snapshot


def preset_cache_key(path: str) -> tuple[object, ...] | None:
    p = str(path or "").strip()
    if not p:
        return None
    try:
        stat = os.stat(p)
    except Exception:
        return None

    digest = ""
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except Exception:
        digest = ""

    return (os.path.normcase(p), int(stat.st_mtime_ns), int(stat.st_size), digest)


def remember_cache_entry(cache: dict, key, value, max_entries: int = 128) -> None:
    if key is None:
        return
    cache[key] = value
    if len(cache) <= max_entries:
        return
    try:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)
    except Exception:
        pass


AT_CONFIG_MAX_FILES = 64


def prune_at_config_cache(
    config_dir: str,
    keep_path: str,
    *,
    filename_prefix: str,
    max_files: int = AT_CONFIG_MAX_FILES,
) -> None:
    try:
        limit = max(1, int(max_files))
    except Exception:
        limit = AT_CONFIG_MAX_FILES

    keep_norm = os.path.normcase(os.path.abspath(str(keep_path or "")))
    entries: list[tuple[bool, int, str, str]] = []

    try:
        with os.scandir(config_dir) as scan:
            for entry in scan:
                if not entry.is_file():
                    continue
                if not entry.name.startswith(filename_prefix) or not entry.name.endswith(".txt"):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                path = os.path.abspath(entry.path)
                norm = os.path.normcase(path)
                entries.append((norm == keep_norm, int(stat.st_mtime_ns), entry.name, path))
    except OSError:
        return

    if len(entries) <= limit:
        return

    has_keep = any(is_keep for is_keep, _mtime, _name, _path in entries)
    keep_count = limit - 1 if has_keep else limit
    newest = sorted(
        (item for item in entries if not item[0]),
        key=lambda item: (item[1], item[2]),
        reverse=True,
    )[:keep_count]
    allowed = {os.path.normcase(os.path.abspath(path)) for _is_keep, _mtime, _name, path in newest}
    if has_keep:
        allowed.add(keep_norm)

    for _is_keep, _mtime, _name, path in entries:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in allowed:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
