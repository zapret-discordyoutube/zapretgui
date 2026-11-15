"""What the helper accepts from a caller: engine options and the files they name."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

PROGRAM_ROOT = Path("/opt/zapretgui")
ENGINE_PATH = PROGRAM_ROOT / "bin" / "nfqws2"

# Set by the helper itself (queue, privileges, logging, process control) or not meant for a service.
REFUSED_OPTIONS = frozenset(
    {
        "--qnum",
        "--daemon",
        "--pidfile",
        "--user",
        "--uid",
        "--debug",
        "--dry-run",
        "--version",
        "--help",
        "--writable",
        "--bind-fix4",
        "--bind-fix6",
    }
)
# Options whose value is a file name as it is (no "@" in front).
PLAIN_FILE_OPTIONS = frozenset(
    {"--hostlist", "--hostlist-exclude", "--hostlist-auto", "--ipset", "--ipset-exclude", "--hostlist-auto-debug"}
)
_AT_PATH = re.compile(r"@([^:,\s]+)")


def option_name(arg: str) -> str:
    return arg.partition("=")[0]


def referenced_paths(arg: str) -> list[str]:
    """Files an argument points at: ``--lua-init=@f``, ``--blob=name:@f``, ``--hostlist=f``."""
    name, separator, value = arg.partition("=")
    if not separator:
        return []
    found = [match.group(1) for match in _AT_PATH.finditer(value)]
    if name in PLAIN_FILE_OPTIONS and not value.startswith("@"):
        found.append(value)
    return found


def _inside(path: str, roots: Iterable[Path]) -> bool:
    try:
        resolved = Path(os.path.realpath(path))
    except (OSError, ValueError):
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def check_args(args: Sequence[str], allowed_roots: Iterable[Path]) -> list[str]:
    """Reasons to refuse (empty list: accepted). Paths must be absolute and, after resolving links, inside a root."""
    roots = tuple(Path(os.path.realpath(root)) for root in allowed_roots)
    problems: list[str] = []
    for arg in args:
        if not isinstance(arg, str) or "\0" in arg or "\n" in arg:
            problems.append("an engine option with forbidden characters")
            continue
        if not arg.startswith("--"):
            problems.append(f"{arg!r}: only --options are accepted")
            continue
        name = option_name(arg)
        if name in REFUSED_OPTIONS:
            problems.append(f"{name} is managed by the helper")
            continue
        if name.startswith("--wf-"):
            continue  # capture options are consumed by the translator, which reads its filter files itself
        for path in referenced_paths(arg):
            if not os.path.isabs(path):
                problems.append(f"{name}: the file {path!r} must be an absolute path")
            elif not _inside(path, roots):
                problems.append(f"{name}: the file {path!r} is outside the allowed folders")
    return problems


def allowed_roots_for(home: str | None, program_root: Path | str = PROGRAM_ROOT) -> list[Path]:
    """The program folder plus the caller's own data folder (where presets, lists and user files live)."""
    roots = [Path(program_root)]
    if home:
        roots.append(Path(home) / ".local" / "share" / "zapretgui")
    return roots
