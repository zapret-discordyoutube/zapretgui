"""What the window uses to talk to the helper."""

from __future__ import annotations

import socket

from platforms.linux.helper.protocol import SOCKET_PATH, ProtocolError, encode, read_message

UNAVAILABLE_HINT = (
    "The Zapret helper service is not running. Start it with: sudo systemctl start zapretgui-helper "
    "(it is enabled for later starts with: sudo systemctl enable zapretgui-helper)."
)
NOT_ALLOWED_HINT = "You are not in the zapretgui group. Add yourself with: sudo usermod -aG zapretgui $USER, then log in again."


class HelperUnavailable(RuntimeError):
    """The helper cannot be reached; the text says what to do."""


class HelperClient:
    def __init__(self, path: str = SOCKET_PATH, *, timeout: float = 25.0) -> None:
        self._path = path
        self._timeout = timeout

    def call(self, op: str, **fields) -> dict:
        message = {"op": op, **fields}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self._timeout)
                conn.connect(self._path)
                conn.sendall(encode(message))
                answer = read_message(conn)
        except PermissionError as exc:
            raise HelperUnavailable(NOT_ALLOWED_HINT) from exc
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise HelperUnavailable(UNAVAILABLE_HINT) from exc
        except (OSError, socket.timeout, ProtocolError) as exc:
            raise HelperUnavailable(f"The helper did not answer: {exc}") from exc
        if answer is None:
            raise HelperUnavailable("The helper closed the connection without an answer.")
        if not answer.get("ok") and "not allowed" in str(answer.get("error", "")):
            raise HelperUnavailable(NOT_ALLOWED_HINT)
        return answer

    def start(self, args: list[str], *, label: str = "") -> dict:
        return self.call("start", args=list(args), label=label)

    def stop(self) -> dict:
        return self.call("stop")

    def status(self) -> dict:
        return self.call("status")

    def cleanup(self) -> dict:
        return self.call("cleanup")

    def dns_set(self, interface: str, servers: list[str]) -> dict:
        return self.call("dns_set", interface=interface, servers=list(servers))

    def dns_flush(self) -> dict:
        return self.call("dns_flush")

    def hosts_write(self, content: str) -> dict:
        return self.call("hosts_write", content=content)
