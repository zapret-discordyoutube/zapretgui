"""The ``system_settings`` interface: Linux (XDG tools and environment), stub, and the neutral callers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from platforms.linux import PLATFORM as LINUX
from platforms.linux import system_settings as linux_settings
from platforms.linux.system_settings import LinuxSystemSettings
from platforms.stub import PLATFORM as STUB
from platforms.windows import PLATFORM as WINDOWS


def test_every_backend_has_system_settings() -> None:
    for backend in (WINDOWS, LINUX, STUB):
        assert backend.system_settings is not None


def test_stub_knows_nothing() -> None:
    s = STUB.system_settings
    assert s.accent_color() is None and s.default_browser_id() == "" and s.proxy_settings() == ("", "")
    assert s.url_scheme_handler_registered("tg") is False


def test_linux_without_the_xdg_tools_answers_unknown(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    s = LinuxSystemSettings()
    assert s.url_scheme_handler_registered("tg") is False
    assert s.default_browser_id() == ""


def test_linux_scheme_handler_comes_from_xdg_mime() -> None:
    done = MagicMock(returncode=0, stdout="org.telegram.desktop.desktop\n")
    with patch("shutil.which", return_value="/usr/bin/xdg-mime"), patch("subprocess.run", return_value=done) as run:
        assert LinuxSystemSettings().url_scheme_handler_registered("tg") is True
    assert run.call_args.args[0] == ["/usr/bin/xdg-mime", "query", "default", "x-scheme-handler/tg"]
    empty = MagicMock(returncode=0, stdout="\n")
    with patch("shutil.which", return_value="/usr/bin/xdg-mime"), patch("subprocess.run", return_value=empty):
        assert LinuxSystemSettings().url_scheme_handler_registered("tg") is False
    failed = MagicMock(returncode=4, stdout="junk")
    with patch("shutil.which", return_value="/usr/bin/xdg-mime"), patch("subprocess.run", return_value=failed):
        assert LinuxSystemSettings().url_scheme_handler_registered("tg") is False


def test_linux_default_browser_is_the_desktop_file_name() -> None:
    done = MagicMock(returncode=0, stdout="firefox.desktop\n")
    with patch("shutil.which", return_value="/usr/bin/xdg-settings"), patch("subprocess.run", return_value=done) as run:
        assert LinuxSystemSettings().default_browser_id() == "firefox.desktop"
    assert run.call_args.args[0][1:] == ["get", "default-web-browser"]


def test_linux_tool_that_hangs_or_cannot_start_is_unknown() -> None:
    import subprocess

    with patch("shutil.which", return_value="/usr/bin/xdg-mime"), patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired("xdg-mime", 3)
    ):
        assert LinuxSystemSettings().url_scheme_handler_registered("tg") is False
    with patch("shutil.which", return_value="/usr/bin/xdg-mime"), patch("subprocess.run", side_effect=OSError("x")):
        assert LinuxSystemSettings().default_browser_id() == ""


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, ("", "")),
        ({"http_proxy": "http://h:3128"}, ("http://h:3128", "")),
        ({"HTTPS_PROXY": "http://s:1", "http_proxy": "http://h:2"}, ("http://s:1", "")),
        ({"https_proxy": "  ", "ALL_PROXY": "socks5://a:1080"}, ("socks5://a:1080", "")),
    ],
)
def test_linux_proxy_comes_from_the_environment(monkeypatch, environment, expected) -> None:
    for name in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert LinuxSystemSettings().proxy_settings() == expected


def test_linux_accent_is_not_read_yet() -> None:
    assert LinuxSystemSettings().accent_color() is None


def test_neutral_callers_ask_the_platform() -> None:
    from blockcheck import default_browser
    from config import telegram_links
    from diagnostics import system_state
    from settings import store
    from telegram_proxy.config import settings as proxy_settings

    fake = MagicMock()
    fake.system_settings.accent_color.return_value = "#112233"
    fake.system_settings.url_scheme_handler_registered.return_value = True
    fake.system_settings.default_browser_id.return_value = "ChromeHTML"
    fake.system_settings.proxy_settings.return_value = ("p:1", "")
    with patch("platforms.current", return_value=fake), patch.object(telegram_links, "current_platform", return_value=fake):
        assert store.get_windows_system_accent() == "#112233"
        assert telegram_links.is_telegram_installed() is True
        assert proxy_settings.is_telegram_link_handler_registered() is True
        assert system_state._read_proxy() == ("p:1", "")
        assert default_browser.default_browser().family == "chrome"
    fake.system_settings.url_scheme_handler_registered.assert_called_with("tg")


def test_a_failing_platform_answer_means_not_installed() -> None:
    from config import telegram_links

    fake = MagicMock()
    fake.system_settings.url_scheme_handler_registered.side_effect = RuntimeError("boom")
    with patch.object(telegram_links, "current_platform", return_value=fake):
        assert telegram_links.is_telegram_installed() is False
