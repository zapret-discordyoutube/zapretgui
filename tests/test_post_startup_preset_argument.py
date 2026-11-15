"""A preset file on the command line is imported through the window's drop path; Linux desktop names of browsers."""
from __future__ import annotations

import pytest

from blockcheck.default_browser import BROWSER_CHROME, BROWSER_EDGE, BROWSER_FIREFOX, BROWSER_OTHER_CHROMIUM, browser_from_prog_id
from main import post_startup_preset_argument as arg


def test_only_existing_preset_files_count_and_switches_are_skipped(tmp_path, monkeypatch):
    zipped = tmp_path / "mine.zip"
    zipped.write_bytes(b"PK")
    text = tmp_path / "other.txt"
    text.write_text("x")
    picture = tmp_path / "pic.png"
    picture.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    found = arg.preset_paths_from_args(["--tray", "mine.zip", str(text), str(picture), "missing.zip", "mine.zip"])
    assert found == [str(zipped), str(text)]


def test_nothing_is_installed_without_a_file(monkeypatch):
    calls = []
    monkeypatch.setattr(arg, "bind_startup_gate", lambda *a, **k: calls.append(a))
    assert arg.install_preset_argument_import(object(), argv=["--tray"]) is False
    assert calls == []


def test_the_window_imports_the_file_after_it_is_ready(tmp_path, monkeypatch):
    zipped = tmp_path / "mine.zip"
    zipped.write_bytes(b"PK")
    imported = []

    class Drop:
        def import_file_paths(self, paths):
            imported.append(paths)

    class Host:
        _preset_file_drop_filter = Drop()

    host = Host()
    ready = []
    monkeypatch.setattr(arg, "bind_startup_gate", lambda signal, callback, is_ready: ready.append(callback))
    monkeypatch.setattr(arg, "schedule_after", lambda delay, callback: callback())
    monkeypatch.setattr(arg, "is_startup_host_alive", lambda h: h is host)
    host.startup_interactive_ready = object()
    assert arg.install_preset_argument_import(host, argv=[str(zipped)]) is True
    assert imported == []  # nothing before the window is ready
    ready[0]()
    assert imported == [[str(zipped)]]


@pytest.mark.parametrize(
    ("desktop", "family", "title"),
    [
        ("firefox.desktop", BROWSER_FIREFOX, "Firefox"),
        ("org.mozilla.firefox.desktop", BROWSER_FIREFOX, "Firefox"),
        ("google-chrome.desktop", BROWSER_CHROME, "Chrome"),
        ("microsoft-edge.desktop", BROWSER_EDGE, "Edge"),
        ("chromium.desktop", BROWSER_OTHER_CHROMIUM, "Chromium"),
        ("brave-browser.desktop", BROWSER_OTHER_CHROMIUM, "Brave"),
        ("ChromeHTML", BROWSER_CHROME, "Chrome"),
        ("MSEdgeHTM", BROWSER_EDGE, "Edge"),
    ],
)
def test_the_default_browser_is_known_by_its_desktop_name_too(desktop, family, title):
    found = browser_from_prog_id(desktop)
    assert (found.family, found.title) == (family, title)
