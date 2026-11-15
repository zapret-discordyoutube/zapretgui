"""The Linux install layout: a read-only program folder plus XDG folders for everything the program writes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.runtime_layout import XDG_LAYOUT_ENV, ApplicationPaths, uses_xdg_layout
from config.xdg_seed import MARKER_NAME, seed_user_data
from core.paths import AppPaths

SRC = Path(__file__).resolve().parents[1] / "src"


def _xdg(tmp_path, **extra):
    env = {"HOME": str(tmp_path / "home")}
    env.update(extra)
    return ApplicationPaths.for_xdg_layout(tmp_path / "opt", env)


def test_windows_layout_is_unchanged(tmp_path) -> None:
    paths = ApplicationPaths.from_root(tmp_path)
    root = tmp_path.resolve()
    assert paths.user_dir == root / "user"
    assert paths.settings_database == root / "user" / "settings.sqlite3"
    assert paths.premium_database == root / "user" / "premium.sqlite3"
    assert paths.logs_dir == root / "user" / "logs" and paths.crash_logs_dir == root / "user" / "logs" / "crashes"
    assert paths.tmp_dir == root / "user" / "tmp" and paths.update_cache_dir == root / "user" / "update_cache"
    assert paths.lists_user_dir == root / "lists" / "user" and paths.lists_dir == root / "lists" == paths.lists_base_dir.parent
    assert paths.presets_dir == root / "presets" == paths.shipped_presets_dir
    assert paths.user_data_root == root
    assert paths.user_lua_dir == root / "user" / "lua" and paths.engine_state_dir == root / "user" / "winws2"


def test_xdg_defaults_follow_the_home_folder(tmp_path) -> None:
    paths = _xdg(tmp_path)
    home = tmp_path / "home"
    assert paths.data_root == home / ".local/share/zapretgui"
    assert paths.config_root == home / ".config/zapretgui"
    assert paths.state_root == home / ".local/state/zapretgui"
    assert paths.cache_root == home / ".cache/zapretgui"


def test_xdg_variables_are_used_and_relative_ones_ignored(tmp_path) -> None:
    paths = _xdg(
        tmp_path,
        XDG_DATA_HOME=str(tmp_path / "d"),
        XDG_CONFIG_HOME="relative/config",  # the specification says: ignore a relative path
        XDG_STATE_HOME=str(tmp_path / "s"),
        XDG_CACHE_HOME="",
    )
    assert paths.data_root == tmp_path / "d" / "zapretgui"
    assert paths.config_root == tmp_path / "home" / ".config/zapretgui"
    assert paths.state_root == tmp_path / "s" / "zapretgui"
    assert paths.cache_root == tmp_path / "home" / ".cache/zapretgui"


def test_every_writable_thing_leaves_the_read_only_program_folder(tmp_path) -> None:
    paths = _xdg(tmp_path)
    program = (tmp_path / "opt").resolve()
    writable = {
        "settings": paths.settings_database,
        "premium": paths.premium_database,
        "logs": paths.logs_dir,
        "crash logs": paths.crash_logs_dir,
        "tmp": paths.tmp_dir,
        "update cache": paths.update_cache_dir,
        "user dir": paths.user_dir,
        "user lua": paths.user_lua_dir,
        "user fakes": paths.user_fakes_dir,
        "engine state": paths.engine_state_dir,
        "user lists": paths.lists_user_dir,
        "final lists": paths.lists_dir,
        "presets": paths.presets_dir,
    }
    for name, path in writable.items():
        assert program not in path.parents and path != program, name
    # and what the installer ships stays under the program folder
    for path in (paths.bin_dir, paths.exe_dir, paths.system_dir, paths.lua_dir, paths.lists_base_dir, paths.shipped_presets_dir):
        assert program in path.parents, path


def test_settings_and_state_live_in_their_own_xdg_folders(tmp_path) -> None:
    paths = _xdg(tmp_path)
    assert paths.settings_database == paths.config_root / "settings.sqlite3"
    assert paths.logs_dir == paths.state_root / "logs"
    assert paths.tmp_dir == paths.cache_root / "tmp"
    assert paths.lists_dir == paths.data_root / "lists" and paths.lists_user_dir == paths.data_root / "lists" / "user"
    assert paths.lists_base_dir == paths.root / "lists" / "base"  # shipped, read-only
    assert paths.presets_dir == paths.data_root / "presets"


def test_engine_presets_follow_the_data_root(tmp_path) -> None:
    paths = _xdg(tmp_path)
    engine = AppPaths(user_root=paths.user_data_root, local_root=paths.root).engine_paths("winws2")
    assert engine.presets_root_dir == paths.data_root / "presets"
    assert engine.user_presets_dir.parent == paths.data_root / "presets"
    assert engine.builtin_presets_dir.parent == paths.data_root / "presets"


@pytest.mark.parametrize(
    ("platform", "packaged", "value", "expected"),
    [
        ("win32", True, "1", False),
        ("win32", False, "1", False),
        ("linux", True, "", True),
        ("linux", False, "", False),
        ("linux", False, "1", True),
        ("linux", False, "yes", True),
        ("linux", False, "0", False),
    ],
)
def test_layout_choice(platform, packaged, value, expected) -> None:
    assert uses_xdg_layout(environ={XDG_LAYOUT_ENV: value}, platform=platform, packaged=packaged) is expected


# -- seeding ---------------------------------------------------------------------------------------


def _ship(paths, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = paths.shipped_presets_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_seed_creates_folders_and_copies_only_builtin_presets(tmp_path) -> None:
    paths = _xdg(tmp_path)
    _ship(paths, {"winws2_builtin/a.txt": "A1", "winws2_builtin/sub/b.txt": "B1", "winws2/shipped_user.txt": "U"})
    assert seed_user_data(paths) is True
    assert (paths.presets_dir / "winws2_builtin" / "a.txt").read_text() == "A1"
    assert (paths.presets_dir / "winws2_builtin" / "sub" / "b.txt").read_text() == "B1"
    assert not (paths.presets_dir / "winws2").exists()  # user presets are not shipped into place
    for directory in (paths.lists_user_dir, paths.config_root, paths.state_root, paths.cache_root):
        assert directory.is_dir()


def test_seed_is_quiet_when_nothing_changed_and_keeps_user_files(tmp_path) -> None:
    paths = _xdg(tmp_path)
    _ship(paths, {"winws2_builtin/a.txt": "A1"})
    assert seed_user_data(paths) is True
    mine = paths.presets_dir / "winws2" / "mine.txt"
    mine.parent.mkdir(parents=True)
    mine.write_text("my preset")
    (paths.presets_dir / "winws2_builtin" / "a.txt").write_text("edited by the program")
    assert seed_user_data(paths) is False
    assert mine.read_text() == "my preset"
    assert (paths.presets_dir / "winws2_builtin" / "a.txt").read_text() == "edited by the program"


def test_seed_replaces_builtin_presets_when_the_shipped_set_changes(tmp_path) -> None:
    paths = _xdg(tmp_path)
    _ship(paths, {"winws2_builtin/a.txt": "A1"})
    seed_user_data(paths)
    mine = paths.presets_dir / "winws2" / "mine.txt"
    mine.parent.mkdir(parents=True)
    mine.write_text("my preset")
    _ship(paths, {"winws2_builtin/a.txt": "A2 longer", "winws2_builtin/new.txt": "N"})
    assert seed_user_data(paths) is True
    assert (paths.presets_dir / "winws2_builtin" / "a.txt").read_text() == "A2 longer"
    assert (paths.presets_dir / "winws2_builtin" / "new.txt").exists()
    assert mine.read_text() == "my preset"
    assert (paths.presets_dir / MARKER_NAME).exists()


def test_seed_does_nothing_in_the_single_folder_layout(tmp_path) -> None:
    paths = ApplicationPaths.from_root(tmp_path)
    assert seed_user_data(paths) is False
    assert not (tmp_path / "user").exists()


def test_seed_without_shipped_presets_still_prepares_the_folders(tmp_path) -> None:
    paths = _xdg(tmp_path)
    assert seed_user_data(paths) is True  # first run: marker written
    assert paths.presets_dir.is_dir() and paths.config_root.is_dir() and (paths.presets_dir / MARKER_NAME).exists()
    assert seed_user_data(paths) is False


# -- the real module in a fresh process --------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux layout")
def test_dev_root_with_the_switch_uses_xdg_folders_in_a_real_process(tmp_path) -> None:
    dev_root = tmp_path / "dev"
    dev_root.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "XDG_", "ZAPRET_"))}
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "ZAPRET_DEV_ROOT": str(dev_root),
            XDG_LAYOUT_ENV: "1",
            "XDG_DATA_HOME": str(tmp_path / "data"),
        }
    )
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
        "import config.runtime_layout as r\n"
        "p = r.APPLICATION_PATHS\n"
        "print(p.root, p.settings_database, p.presets_dir, p.logs_dir, sep='|')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    root, settings, presets, logs = done.stdout.strip().split("|")
    assert Path(root) == dev_root.resolve()
    assert Path(settings) == tmp_path / "home" / ".config/zapretgui/settings.sqlite3"
    assert Path(presets) == tmp_path / "data" / "zapretgui" / "presets"
    assert Path(logs) == tmp_path / "home" / ".local/state/zapretgui/logs"


def test_settings_database_follows_the_layout_unless_a_test_moves_the_root(tmp_path, monkeypatch) -> None:
    from settings import store

    paths = _xdg(tmp_path)
    monkeypatch.setattr(store, "APPLICATION_PATHS", paths)
    monkeypatch.setattr(store, "MAIN_DIRECTORY", str(paths.root))
    assert store.get_settings_database_path() == paths.config_root / "settings.sqlite3"
    # tests move only MAIN_DIRECTORY to keep away from the live database: the old rule applies then
    monkeypatch.setattr(store, "MAIN_DIRECTORY", str(tmp_path / "elsewhere"))
    assert store.get_settings_database_path() == tmp_path / "elsewhere" / "user" / "settings.sqlite3"


def test_premium_database_sits_next_to_the_settings_database(tmp_path, monkeypatch) -> None:
    from settings import store

    paths = _xdg(tmp_path)
    monkeypatch.setattr(store, "APPLICATION_PATHS", paths)
    monkeypatch.setattr(store, "MAIN_DIRECTORY", str(paths.root))
    assert store.get_settings_database_path().parent / "premium.sqlite3" == paths.premium_database


def test_update_state_falls_back_to_the_cache_folder_in_the_xdg_layout(tmp_path) -> None:
    from config.runtime_layout import resolve_update_state_dir

    paths = _xdg(tmp_path)
    chosen = resolve_update_state_dir(
        channel="stable", application_root=paths.root, program_data=None, local_app_data=None, fallback_dir=paths.update_cache_dir
    )
    assert chosen == paths.cache_root / "update_cache"
    assert resolve_update_state_dir(channel="stable", application_root=tmp_path, program_data=None, local_app_data=None) == (
        tmp_path / "user" / "update_cache"
    )
