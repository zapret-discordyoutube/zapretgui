"""Окно приёма сервера (``utils.tcp_info``): на Windows — из SIO_TCP_INFO, иначе честное «не известно»."""

from __future__ import annotations

import socket
import sys
import unittest

from utils import tcp_info


class ServerWindowTests(unittest.TestCase):
    def test_unknown_outside_windows_and_without_a_connection(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]

        window = tcp_info.server_window("127.0.0.1", port=port, timeout=1.0)

        if sys.platform != "win32":
            self.assertIsNone(window)
        else:
            self.assertGreater(window or 0, 0)
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        free_port = closed.getsockname()[1]
        closed.close()
        self.assertIsNone(tcp_info.server_window("127.0.0.1", port=free_port, timeout=1.0))


if __name__ == "__main__":
    unittest.main()
