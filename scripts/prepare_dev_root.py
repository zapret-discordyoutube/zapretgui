"""Prepare a folder to run ZapretGUI from source on Linux (development only).

The program refuses to start from source on purpose. Outside Windows there is one
visible exception: set ``ZAPRET_DEV_ROOT`` to a folder with the same layout an
install has. This script builds that folder from the repositories, the way the
release build builds the installer stage (``build_zapret/release_pipeline.py``):

    python3 scripts/prepare_dev_root.py /tmp/zapret-dev-root
    ZAPRET_DEV_ROOT=/tmp/zapret-dev-root PYTHONPATH=src python3 src/main.py

Everything is copied, so the program can write into the folder (settings, logs,
user presets) without touching the repository. Run the script again to refresh
the read-only parts; ``user/`` and the user presets are left alone.

The private repository (``private_zapretgui``) supplies Lua files, engine blobs,
icons, profile templates and the catalog databases. By default it is looked for
next to this repository; pass ``--private PATH`` to point elsewhere. Without it the
program still starts, but those parts are missing.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PUBLIC_ROOT / "src"))

from config.runtime_layout import ApplicationPaths  # noqa: E402


def _normalize_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _combined_list_text(base_text: str) -> str:
    lines = [
        line
        for raw in _normalize_text(base_text).splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _copy_tree(source: Path, destination: Path, report: list[str], label: str, pattern: str = "*") -> None:
    if not source.is_dir():
        report.append(f"missing: {label} ({source})")
        return
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for item in sorted(source.rglob(pattern)):
        if not item.is_file() or "__pycache__" in item.parts:
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    report.append(f"copied {count:4d} files: {label}")


def _copy_file(source: Path, destination: Path, report: list[str], label: str) -> None:
    if not source.is_file():
        report.append(f"missing: {label} ({source})")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    report.append(f"copied      1 file : {label}")


def _write_managed_lists(paths: ApplicationPaths, report: list[str]) -> None:
    from lists.core.embedded_defaults import (
        get_ipset_all_base_text,
        get_ipset_ru_base_text,
        get_other_base_text,
    )

    paths.lists_base_dir.mkdir(parents=True, exist_ok=True)
    paths.lists_user_dir.mkdir(parents=True, exist_ok=True)
    managed = {
        "other.txt": _normalize_text(get_other_base_text()),
        "ipset-all.txt": _normalize_text(get_ipset_all_base_text()),
        "ipset-ru.txt": _normalize_text(get_ipset_ru_base_text()),
    }
    for file_name, base_text in managed.items():
        (paths.lists_base_dir / file_name).write_text(base_text, encoding="utf-8")
        (paths.lists_dir / file_name).write_text(_combined_list_text(base_text), encoding="utf-8")
    report.append(f"wrote  {len(managed):4d} files: managed lists")


def prepare(root: Path, private: Path | None) -> list[str]:
    """Builds the folder and returns a report: one line per part, 'missing:' for what was not found."""
    paths = ApplicationPaths.from_root(root)
    public_src = PUBLIC_ROOT / "src"
    report: list[str] = []

    _copy_tree(public_src / "system" / "strategy_catalogs", paths.strategy_catalogs_dir, report, "strategy catalogs", "*.txt")
    _copy_tree(public_src / "system" / "ico", paths.ico_dir, report, "icons (sidebar)")
    for engine in ("winws2", "winws1"):
        _copy_tree(
            public_src / "presets" / "builtin" / engine,
            paths.presets_dir / f"{engine}_builtin",
            report,
            f"built-in presets {engine}",
            "*.txt",
        )
        # User presets live next to the built-in ones; created empty, never overwritten.
        (paths.presets_dir / engine).mkdir(parents=True, exist_ok=True)

    if private is not None:
        _copy_tree(private / "dist" / "lua", paths.lua_dir, report, "Lua files")
        _copy_tree(private / "dist" / "bin", paths.bin_dir, report, "engine blobs (bin)")
        _copy_tree(private / "dist" / "windivert.filter", paths.root / "windivert.filter", report, "capture filters (windivert.filter)")
        _copy_tree(private / "dist" / "ico", paths.ico_dir, report, "icons (application)")
        _copy_tree(private / "resources" / "profile" / "templates", paths.profile_templates_dir, report, "profile templates", "*.txt")
        _copy_file(private / "resources" / "system" / "hosts_catalog.sqlite3", paths.hosts_catalog_database, report, "hosts catalog")
        _copy_file(private / "resources" / "system" / "fakes_catalog.sqlite3", paths.fakes_catalog_database, report, "fakes catalog")
    else:
        report.append("missing: private repository (Lua, blobs, icons, templates, catalogs)")

    _write_managed_lists(paths, report)
    paths.user_dir.mkdir(parents=True, exist_ok=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a ZAPRET_DEV_ROOT folder for running from source on Linux.")
    parser.add_argument("root", type=Path, help="folder to create or refresh")
    parser.add_argument("--private", type=Path, default=None, help="path of private_zapretgui (default: next to this repository)")
    args = parser.parse_args(argv)

    private = args.private or PUBLIC_ROOT.parent / "private_zapretgui"
    root = args.root.expanduser().resolve()
    protected = (PUBLIC_ROOT, PUBLIC_ROOT / "src", PUBLIC_ROOT / "tests")
    if any(folder in (root, *root.parents) for folder in protected):
        print(f"Refusing to use {root}: pick a folder outside the repository.", file=sys.stderr)
        return 2

    report = prepare(root, private if private.is_dir() else None)
    print(f"ZAPRET_DEV_ROOT folder: {root}")
    print("\n".join(report))
    print(f"\nRun:  ZAPRET_DEV_ROOT={root} PYTHONPATH={PUBLIC_ROOT / 'src'} python3 {PUBLIC_ROOT / 'src' / 'main.py'}")
    return 1 if any(line.startswith("missing:") for line in report) else 0


if __name__ == "__main__":
    sys.exit(main())
