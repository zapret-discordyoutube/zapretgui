"""The hosts file behind Platform.hosts: Linux writes through the helper without a BOM; hosts.py asks the platform."""
from __future__ import annotations

from pathlib import Path

import pytest

from platforms.linux.helper.client import HelperUnavailable
from platforms.linux.hosts_file import LinuxHostsFile

HOSTS = Path("/etc/hosts")


class FakeHelper:
    def __init__(self, answer=None, unavailable=False):
        self.answer = answer or {"ok": True}
        self.unavailable = unavailable
        self.written = []

    def hosts_write(self, content):
        if self.unavailable:
            raise HelperUnavailable("The Zapret helper service is not running.")
        self.written.append(content)
        return self.answer

    def status(self):
        if self.unavailable:
            raise HelperUnavailable("The Zapret helper service is not running.")
        return self.answer


def test_linux_hosts_is_etc_hosts_without_a_byte_order_mark():
    backend = LinuxHostsFile(FakeHelper())
    assert backend.default_path() == HOSTS
    assert backend.encoding == "utf-8"  # glibc reads a BOM as part of the first address
    assert backend.is_read_only(HOSTS) is False
    assert backend.check_name(HOSTS) == (True, None)


def test_linux_write_sends_the_text_to_the_helper():
    helper = FakeHelper()
    LinuxHostsFile(helper).write(HOSTS, "127.0.0.1 localhost\n1.2.3.4 пример.test\n".encode("utf-8"))
    assert helper.written == ["127.0.0.1 localhost\n1.2.3.4 пример.test\n"]


def test_linux_write_refuses_other_files_and_reports_the_helper_text():
    helper = FakeHelper()
    with pytest.raises(PermissionError):
        LinuxHostsFile(helper).write(Path("/etc/passwd"), b"x")
    assert helper.written == []
    with pytest.raises(PermissionError, match="helper"):
        LinuxHostsFile(FakeHelper(unavailable=True)).write(HOSTS, b"127.0.0.1 localhost\n")
    with pytest.raises(PermissionError, match="localhost line"):
        LinuxHostsFile(FakeHelper({"ok": False, "error": "the hosts file must keep the localhost line"})).write(HOSTS, b"x\n")


def test_linux_can_write_means_the_helper_answers():
    LinuxHostsFile(FakeHelper()).can_write(HOSTS)
    with pytest.raises(PermissionError):
        LinuxHostsFile(FakeHelper(unavailable=True)).can_write(HOSTS)


def test_linux_has_no_access_rights_button_to_press():
    done, text = LinuxHostsFile(FakeHelper()).restore_access(HOSTS)
    assert done is False and "systemctl status zapretgui-helper" in text


def test_safe_write_encodes_for_the_platform_and_hands_over_the_bytes(monkeypatch):
    import hosts.hosts as hosts_module

    seen = []

    class Backend:
        encoding = "utf-8"

        def default_path(self):
            return HOSTS

        def is_read_only(self, path):
            return False

        def write(self, path, data):
            seen.append((path, data))

    monkeypatch.setattr(hosts_module, "_platform_hosts", lambda: Backend())
    monkeypatch.setattr(hosts_module, "HOSTS_PATH", Path("/nonexistent/hosts-for-test"))
    assert hosts_module.safe_write_hosts_file("127.0.0.1 localhost\r\n1.2.3.4 a.test\r\n") is True
    assert seen == [(Path("/nonexistent/hosts-for-test"), b"127.0.0.1 localhost\n1.2.3.4 a.test\n")]  # no BOM, LF only


def test_safe_write_reports_a_refusal_as_false(monkeypatch):
    import hosts.hosts as hosts_module

    class Backend:
        encoding = "utf-8"

        def is_read_only(self, path):
            return False

        def write(self, path, data):
            raise PermissionError("no helper")

    monkeypatch.setattr(hosts_module, "_platform_hosts", lambda: Backend())
    monkeypatch.setattr(hosts_module, "HOSTS_PATH", Path("/nonexistent/hosts-for-test"))
    assert hosts_module.safe_write_hosts_file("127.0.0.1 localhost\n") is False


def test_windows_backend_keeps_the_bom_and_writes_in_place(tmp_path):
    from platforms.windows.hosts_file import WindowsHostsFile

    backend = WindowsHostsFile()
    assert backend.encoding == "utf-8-sig"
    target = tmp_path / "hosts"
    target.write_bytes(b"old\n")
    backend.write(target, "new\n".encode("utf-8-sig"))
    assert target.read_bytes() == b"\xef\xbb\xbfnew\n"
    assert backend.check_name(target) == (True, None)
    assert backend.is_read_only(target) is False
    backend.can_write(target)
