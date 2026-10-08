"""Где перехватывают обычные DNS-запросы: место по сроку жизни пакета."""

from __future__ import annotations

import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from diagnostics import dns_place as dp


def _facts(answers_from: int | None, distance: int, max_ttl: int = 12):
    with ThreadPoolExecutor(12) as pool:
        return dp.collect(
            lambda ttl: answers_from is not None and ttl >= answers_from, submit=pool.submit, distance=distance, max_ttl=max_ttl
        )


class JudgeTests(unittest.TestCase):
    def test_answer_before_the_packet_could_reach_the_server_is_an_interception(self) -> None:
        verdict = dp.judge(_facts(3, distance=9))

        self.assertEqual((verdict.code, verdict.hop), (dp.PLACE_FOUND, 3))
        self.assertIn("между узлами 2 и 3", verdict.text)
        self.assertEqual(dp.judge(_facts(1, distance=9)).hop, 1)
        self.assertIn("роутер", dp.judge(_facts(1, distance=9)).text)

    def test_answer_only_from_the_server_distance_is_honest(self) -> None:
        verdict = dp.judge(_facts(9, distance=9))

        self.assertEqual((verdict.code, verdict.hop), (dp.PLACE_NONE, None))

    def test_without_distance_or_answers_there_is_no_verdict(self) -> None:
        self.assertEqual(dp.judge(_facts(4, distance=0)).code, dp.PLACE_UNKNOWN)
        self.assertEqual(dp.judge(_facts(None, distance=9)).code, dp.PLACE_UNKNOWN)
        self.assertIsNone(dp.judge(None))
        with ThreadPoolExecutor(4) as pool:
            cancelled = dp.collect(lambda ttl: None, submit=pool.submit, distance=9, max_ttl=3)
        self.assertIsNone(dp.judge(cancelled))


class AskTests(unittest.TestCase):
    def test_only_a_real_answer_to_this_query_counts(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(2)
        port = server.getsockname()[1]
        self.addCleanup(server.close)
        mode = ["answer"]

        def serve() -> None:
            for _attempt in range(2):
                try:
                    data, peer = server.recvfrom(512)
                except OSError:
                    return
                if mode[0] == "answer":
                    server.sendto(data[:2] + b"\x81\x80" + data[4:], peer)
                else:
                    # Чужой номер запроса: это не ответ на наш вопрос.
                    server.sendto(b"\x00\x00\x81\x80" + data[4:], peer)

        threading.Thread(target=serve, daemon=True).start()
        original = socket.socket.connect

        def to_test_port(sock, address):
            return original(sock, (address[0], port))

        socket.socket.connect = to_test_port
        self.addCleanup(setattr, socket.socket, "connect", original)

        self.assertIs(dp.ask_with_ttl("127.0.0.1", 64, timeout=1.0), True)
        mode[0] = "foreign"
        self.assertIs(dp.ask_with_ttl("127.0.0.1", 64, timeout=0.4), False)


if __name__ == "__main__":
    unittest.main()


class SectionTests(unittest.TestCase):
    def test_distance_comes_from_the_trace_to_the_dns_server(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from diagnostics import sections
        from diagnostics.path_trace import Hop, RouteTrace

        route = RouteTrace(dp.DNS_SERVER, tuple(Hop(ttl, "router", f"10.0.0.{ttl}") for ttl in range(1, 9)), reached=True)
        with ThreadPoolExecutor(24) as pool:
            run = SimpleNamespace(submit=pool.submit, probe_cancel=None)
            with patch.object(dp, "ask_with_ttl", lambda server, ttl, **_k: ttl >= 2):
                verdict = sections._dns_place(run, lambda ip: route)
                # Трассировка до сервера не дошла — расстояния нет, и перехват с сервером не различить.
                unknown = sections._dns_place(run, lambda ip: RouteTrace(dp.DNS_SERVER, (), reached=False))

        self.assertEqual((verdict.code, verdict.hop), (dp.PLACE_FOUND, 2))
        self.assertEqual(unknown.code, dp.PLACE_UNKNOWN)
