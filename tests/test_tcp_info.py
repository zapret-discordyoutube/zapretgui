"""Окно приёма сервера (``platforms.current().netprobe.tcp_server_window``): Windows — SIO_TCP_INFO, Linux — TCP_INFO (ядро 6.2+), иначе честное «не известно»."""

from __future__ import annotations

import socket
import sys
import unittest

from platforms import current as current_platform


class ServerWindowTests(unittest.TestCase):
    def test_unknown_outside_windows_and_without_a_connection(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]

        window = current_platform().netprobe.tcp_server_window("127.0.0.1", port=port, timeout=1.0)

        if sys.platform == "win32":
            self.assertGreater(window or 0, 0)
        else:
            # Linux: the peer window appears in TCP_INFO from kernel 6.2; older kernels answer "unknown".
            self.assertTrue(window is None or window > 0)
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        free_port = closed.getsockname()[1]
        closed.close()
        self.assertIsNone(current_platform().netprobe.tcp_server_window("127.0.0.1", port=free_port, timeout=1.0))


if __name__ == "__main__":
    unittest.main()
