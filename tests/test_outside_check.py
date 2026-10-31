"""«Жив ли сайт снаружи»: разбор ответа сервера и случаи, когда вывода нет."""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from diagnostics import outside_check as oc
from utils.socket_cancel import SocketCancel


def _answer(*results, self_ok=True) -> bytes:
    return json.dumps({"v": 1, "results": list(results), "self_ok": self_ok}).encode()


class AskTests(unittest.TestCase):
    def _ask(self, hosts, answer, ips=("83.217.211.149",)):
        with patch.object(oc, "_post", return_value=answer) as post:
            return oc.ask(hosts, server_ips=lambda: ips, cancel=SocketCancel()), post

    def test_alive_and_dead_sites(self) -> None:
        found, post = self._ask(
            ["discord.com", "dead.example", "discord.com"],
            _answer({"host": "discord.com", "dns": "ok", "up": True}, {"host": "dead.example", "dns": "ok", "up": False}),
        )

        self.assertEqual(found, {"discord.com": oc.OUTSIDE_UP, "dead.example": oc.OUTSIDE_DOWN})
        # Повтор имени не отправляется дважды.
        self.assertEqual(json.loads(post.call_args.args[1])["hosts"], ["discord.com", "dead.example"])

    def test_no_conclusion_without_servers_own_network_or_dns(self) -> None:
        found, _post = self._ask(["discord.com"], _answer({"host": "discord.com", "dns": "ok", "up": False}, self_ok=False))
        self.assertEqual(found, {})

        found, _post = self._ask(["discord.com"], _answer({"host": "discord.com", "dns": "fail", "up": False}))
        self.assertEqual(found, {})

    def test_broken_or_missing_answer_gives_nothing(self) -> None:
        for answer in (None, b"", b"not json", b"[]"):
            with self.subTest(answer=answer):
                self.assertEqual(self._ask(["discord.com"], answer)[0], {})
        self.assertEqual(self._ask(["discord.com"], _answer(), ips=())[0], {})

    def test_no_more_than_eight_sites_go_to_the_server(self) -> None:
        hosts = [f"site{index}.example" for index in range(12)]
        _found, post = self._ask(hosts, _answer())

        self.assertEqual(len(json.loads(post.call_args.args[1])["hosts"]), oc.MAX_HOSTS)


class StopTests(unittest.TestCase):
    def test_stop_breaks_a_hanging_tls_handshake(self) -> None:
        # Сервер принимает соединение и молчит: рукопожатие TLS ждало бы 30 с.
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        accepted: list[socket.socket] = []
        threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True).start()
        real_socket = socket.socket

        class _LocalSocket(real_socket):
            def connect(self, _address):
                return super().connect(("127.0.0.1", port))

        cancel = SocketCancel()
        result: list[object] = []
        try:
            with patch.object(oc.socket, "socket", _LocalSocket):
                worker = threading.Thread(target=lambda: result.append(oc._post("127.0.0.1", b"{}", cancel)), daemon=True)
                started = time.monotonic()
                worker.start()
                time.sleep(0.3)
                cancel.cancel()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(result, [None])
        finally:
            server.close()
            for conn in accepted:
                conn.close()


if __name__ == "__main__":
    unittest.main()
