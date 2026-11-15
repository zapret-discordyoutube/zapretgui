"""The hosts file on Linux: ``/etc/hosts``, owned by root, so every write goes through the helper.

There is no byte-order mark here (glibc would read ``\\ufeff127.0.0.1`` as a broken first line) and no "read only"
attribute or access-rights button: when the write fails, the text says to check the helper service.
"""

from __future__ import annotations

from pathlib import Path

HOSTS_PATH = "/etc/hosts"
CHECK_SERVICE_HINT = "Check that the helper service runs: sudo systemctl status zapretgui-helper"


class LinuxHostsFile:
    encoding = "utf-8"

    def __init__(self, helper=None) -> None:
        self._helper = helper

    def _helper_client(self):
        if self._helper is None:
            from platforms.linux.helper.client import HelperClient

            self._helper = HelperClient()
        return self._helper

    def default_path(self) -> Path:
        return Path(HOSTS_PATH)

    def is_read_only(self, path: Path) -> bool:
        return False

    def write(self, path: Path, data: bytes) -> None:
        """Hand the text to the helper. ``PermissionError`` says what to do when that is not possible."""
        from platforms.linux.helper.client import HelperUnavailable

        if Path(path) != Path(HOSTS_PATH):
            raise PermissionError(f"only {HOSTS_PATH} can be written")
        try:
            text = data.decode("utf-8")
            answer = self._helper_client().hosts_write(text)
        except UnicodeDecodeError as exc:
            raise ValueError(f"the hosts text is not UTF-8: {exc}") from exc
        except HelperUnavailable as exc:
            raise PermissionError(str(exc)) from exc
        if not answer.get("ok"):
            raise PermissionError(str(answer.get("error") or "the helper refused to write the hosts file"))

    def check_name(self, path: Path):
        return True, None

    def can_write(self, path: Path) -> None:
        from platforms.linux.helper.client import HelperUnavailable

        try:
            answer = self._helper_client().status()
        except HelperUnavailable as exc:
            raise PermissionError(str(exc)) from exc
        if not answer.get("ok"):
            raise PermissionError(CHECK_SERVICE_HINT)

    def restore_access(self, path: Path):
        return False, "Nothing to restore here: /etc/hosts is written by the helper service. " + CHECK_SERVICE_HINT
