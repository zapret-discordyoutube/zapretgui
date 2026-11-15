"""The hosts page says "Windows list" only on Windows."""
from __future__ import annotations

from app.ui_texts import TEXTS
from hosts.ui import page
from platforms.stub import make_platform


def test_the_subtitle_names_windows_only_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("platforms.current", lambda: make_platform(name="windows"))
    assert page._subtitle_key() == "page.hosts.subtitle"
    monkeypatch.setattr("platforms.current", lambda: make_platform(name="linux"))
    assert page._subtitle_key() == "page.hosts.subtitle.basic"
    for language in ("ru", "en"):
        assert "Windows" in TEXTS["page.hosts.subtitle"][language]
        assert "Windows" not in TEXTS["page.hosts.subtitle.basic"][language]
