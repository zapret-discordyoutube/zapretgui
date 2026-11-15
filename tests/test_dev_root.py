"""Running from source outside Windows: the ZAPRET_DEV_ROOT switch and the script that prepares its folder."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.runtime_layout import DEV_ROOT_ENV, resolve_dev_root

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


# --- resolve_dev_root -----------------------------------------------------------

def test_no_variable_means_no_dev_root():
    assert resolve_dev_root(environ={}, platform="linux") == (None, "")
    assert resolve_dev_root(environ={DEV_ROOT_ENV: "   "}, platform="linux") == (None, "")


def test_existing_folder_is_used(tmp_path):
    root, error = resolve_dev_root(environ={DEV_ROOT_ENV: str(tmp_path)}, platform="linux")
    assert root == tmp_path.resolve() and error == ""


def test_missing_folder_gives_a_clear_error(tmp_path):
    root, error = resolve_dev_root(environ={DEV_ROOT_ENV: str(tmp_path / "nope")}, platform="linux")
    assert root is None
    assert DEV_ROOT_ENV in error and "nope" in error


def test_windows_ignores_the_variable(tmp_path):
    assert resolve_dev_root(environ={DEV_ROOT_ENV: str(tmp_path)}, platform="win32") == (None, "")


# --- the real module in a fresh process (the paths are computed at import) -------

def _run(code: str, env_value: str | None):
    env = {k: v for k, v in os.environ.items() if k != DEV_ROOT_ENV and not k.startswith("PYTHON")}
    if env_value is not None:
        env[DEV_ROOT_ENV] = env_value
    script = f"import sys; sys.path.insert(0, {str(SRC)!r})\nimport config.runtime_layout as r\n{code}"
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)


@pytest.mark.skipif(sys.platform == "win32", reason="the switch does not exist on Windows")
def test_source_start_is_still_refused_without_the_variable():
    done = _run("r.require_packaged_application()", None)
    assert done.returncode != 0
    assert "SourceApplicationLaunchForbidden" in done.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="the switch does not exist on Windows")
def test_variable_opens_the_gate_and_moves_every_path(tmp_path):
    code = (
        "r.require_packaged_application()\n"
        "print(r.is_dev_root_run(), r.APPLICATION_ROOT, r.APPLICATION_PATHS.presets_dir, r.APPLICATION_RESOURCE_PATHS.root)"
    )
    done = _run(code, str(tmp_path))
    assert done.returncode == 0, done.stderr
    flag, root, presets, resources = done.stdout.split()
    assert flag == "True"
    assert Path(root) == tmp_path.resolve()
    assert Path(presets) == tmp_path.resolve() / "presets"
    assert Path(resources) == tmp_path.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="the switch does not exist on Windows")
def test_bad_variable_is_reported_by_name_not_as_a_generic_refusal(tmp_path):
    done = _run("r.require_packaged_application()", str(tmp_path / "missing"))
    assert done.returncode != 0
    assert DEV_ROOT_ENV in done.stderr and "missing" in done.stderr


# --- scripts/prepare_dev_root.py ---------------------------------------------------

@pytest.fixture(scope="module")
def prepare_module():
    spec = importlib.util.spec_from_file_location("prepare_dev_root", REPO / "scripts" / "prepare_dev_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_builds_the_layout_the_program_reads(prepare_module, tmp_path):
    from config.runtime_layout import ApplicationPaths

    report = prepare_module.prepare(tmp_path / "root", None)
    paths = ApplicationPaths.from_root(tmp_path / "root")

    assert any(p.is_file() for p in (paths.presets_dir / "winws2_builtin").glob("*.txt"))
    assert (paths.presets_dir / "winws2").is_dir() and (paths.presets_dir / "winws1").is_dir()
    assert any((paths.strategy_catalogs_dir / "winws2").glob("*.txt"))
    for name in ("other.txt", "ipset-all.txt", "ipset-ru.txt"):
        assert (paths.lists_base_dir / name).is_file()
        assert (paths.lists_dir / name).is_file()
    assert paths.lists_user_dir.is_dir() and paths.user_dir.is_dir()
    # Without the private repository the report says so, instead of pretending.
    assert any(line.startswith("missing: private repository") for line in report)


def test_prepare_keeps_user_state_and_user_presets_on_refresh(prepare_module, tmp_path):
    from config.runtime_layout import ApplicationPaths

    root = tmp_path / "root"
    prepare_module.prepare(root, None)
    paths = ApplicationPaths.from_root(root)
    (paths.user_dir / "settings.sqlite3").write_text("mine", encoding="utf-8")
    (paths.presets_dir / "winws2" / "my.txt").write_text("--new", encoding="utf-8")

    prepare_module.prepare(root, None)

    assert (paths.user_dir / "settings.sqlite3").read_text(encoding="utf-8") == "mine"
    assert (paths.presets_dir / "winws2" / "my.txt").read_text(encoding="utf-8") == "--new"


def test_prepare_copies_private_parts_when_given(prepare_module, tmp_path):
    from config.runtime_layout import ApplicationPaths

    private = tmp_path / "private"
    (private / "dist" / "lua").mkdir(parents=True)
    (private / "dist" / "lua" / "x.lua").write_text("-- lua", encoding="utf-8")
    (private / "resources" / "system").mkdir(parents=True)
    (private / "resources" / "system" / "hosts_catalog.sqlite3").write_bytes(b"db")

    report = prepare_module.prepare(tmp_path / "root", private)
    paths = ApplicationPaths.from_root(tmp_path / "root")

    assert (paths.lua_dir / "x.lua").read_text(encoding="utf-8") == "-- lua"
    assert paths.hosts_catalog_database.read_bytes() == b"db"
    assert any(line.startswith("missing: fakes catalog") for line in report)


def test_prepare_refuses_a_folder_inside_the_repository(prepare_module, capsys):
    assert prepare_module.main([str(SRC / "dev-root")]) == 2
    assert "outside the repository" in capsys.readouterr().err
