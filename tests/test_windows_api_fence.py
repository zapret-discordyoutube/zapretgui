"""Windows-only calls are fenced: the list of files that still have them only shrinks."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import architecture_checks as checks


@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    (tmp_path / "src" / "platforms" / "windows").mkdir(parents=True)
    listing = tmp_path / "windows_api_files.txt"
    monkeypatch.setattr(checks, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(checks, "SRC_ROOT", tmp_path / "src")
    monkeypatch.setattr(checks, "WINDOWS_API_LIST", listing)
    return tmp_path, listing


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run() -> list[str]:
    files = sorted((checks.SRC_ROOT).rglob("*.py"))
    return [problem.message for problem in checks.check_windows_api_stays_in_platform_modules(files)]


def test_real_tree_matches_the_list():
    assert checks.WINDOWS_API_LIST.exists()
    # The real repository must agree with its own list, otherwise the fence is decoration.
    problems = checks.check_windows_api_stays_in_platform_modules(checks._python_files())
    assert [p.format() for p in problems] == []


@pytest.mark.parametrize(
    "line",
    [
        "ctypes.windll.user32.MessageBoxW(0, 'x', 'y', 0)",
        "import winreg",
        "from winreg import HKEY_CURRENT_USER",
        "key = winreg.OpenKey(root, path)",
        "subprocess.run(['powershell', '-Command', 'x'])",
        "run('sc.exe query zapret')",
        "run('taskkill /F /IM winws2.exe')",
        "run('schtasks /query')",
        "import win32api",
        "from ctypes import wintypes",
    ],
)
def test_pattern_sees_windows_only_calls(line):
    assert checks.WINDOWS_API_PATTERN.search(line)


@pytest.mark.parametrize(
    "line",
    ["import os", "winreg_stub_note = 1", "log('process started')", "x = shell_window"],
)
def test_pattern_ignores_ordinary_code(line):
    assert not checks.WINDOWS_API_PATTERN.search(line)


def test_new_windows_call_in_unlisted_file_is_an_error(fake_repo):
    root, listing = fake_repo
    listing.write_text("", encoding="utf-8")
    _write(root, "src/feature/new_thing.py", "import ctypes\nctypes.windll.kernel32.Beep(1, 1)\n")
    problems = _run()
    assert len(problems) == 1 and "outside src/platforms/windows/" in problems[0]


def test_listed_file_with_same_count_is_fine(fake_repo):
    root, listing = fake_repo
    _write(root, "src/feature/old.py", "ctypes.windll.kernel32.Beep(1, 1)\n")
    listing.write_text("src/feature/old.py 1\n", encoding="utf-8")
    assert _run() == []


def test_listed_file_that_grows_is_an_error(fake_repo):
    root, listing = fake_repo
    _write(root, "src/feature/old.py", "ctypes.windll.a()\nctypes.windll.b()\n")
    listing.write_text("src/feature/old.py 1\n", encoding="utf-8")
    problems = _run()
    assert len(problems) == 1 and "grew from 1 to 2" in problems[0]


def test_listed_file_that_shrinks_must_be_recorded(fake_repo):
    root, listing = fake_repo
    _write(root, "src/feature/old.py", "ctypes.windll.a()\n")
    listing.write_text("src/feature/old.py 3\n", encoding="utf-8")
    problems = _run()
    assert len(problems) == 1 and "shrank from 3 to 1" in problems[0]


def test_listed_file_without_calls_must_leave_the_list(fake_repo):
    root, listing = fake_repo
    _write(root, "src/feature/old.py", "x = 1\n")
    listing.write_text("src/feature/old.py 2\n", encoding="utf-8")
    problems = _run()
    assert len(problems) == 1 and "no longer has Windows-only calls" in problems[0]


def test_windows_backend_folder_is_the_allowed_home(fake_repo):
    root, listing = fake_repo
    listing.write_text("", encoding="utf-8")
    _write(root, "src/platforms/windows/autostart.py", "import winreg\nwinreg.OpenKey(1, 'x')\n")
    assert _run() == []


def test_refresh_writes_what_the_scan_finds(fake_repo, capsys):
    root, listing = fake_repo
    _write(root, "src/feature/old.py", "ctypes.windll.a()\nctypes.windll.b()\n")
    checks.refresh_windows_api_list()
    assert "src/feature/old.py 2" in listing.read_text(encoding="utf-8")
    assert _run() == []
