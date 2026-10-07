from __future__ import annotations

import socket
import struct
import threading
import unittest

from diagnostics import path_trace as pt
from utils.socket_cancel import SocketCancel
from utils.windows_icmp import HOP_ROUTER, HOP_SILENT, HOP_TARGET, HOP_UNSUPPORTED, TraceHopResult

TARGET = "203.0.113.50"


def _probe(path: dict[int, TraceHopResult]):
    """Учебная сеть: что отвечает на пакет с данным сроком жизни."""
    calls: list[int] = []
    lock = threading.Lock()

    def probe(ip, ttl, *, timeout_ms):
        with lock:
            calls.append(ttl)
        return path.get(ttl, TraceHopResult(HOP_SILENT))

    probe.calls = calls
    return probe


def _router(index: int, rtt: float = 1.0) -> TraceHopResult:
    return TraceHopResult(HOP_ROUTER, f"10.0.0.{index}", rtt)


class TraceRouteTests(unittest.TestCase):
    def test_hops_are_listed_up_to_the_target(self) -> None:
        path = {1: _router(1), 2: _router(2), 3: TraceHopResult(HOP_TARGET, TARGET, 40.0), 4: TraceHopResult(HOP_TARGET, TARGET, 40.0)}
        trace = pt.trace_route(TARGET, max_hops=8, probe=_probe(path))

        self.assertTrue(trace.reached and trace.supported)
        self.assertEqual([(hop.ttl, hop.address) for hop in trace.hops], [(1, "10.0.0.1"), (2, "10.0.0.2"), (3, TARGET)])
        self.assertEqual(trace.hop(2).rtt_ms, 1.0)
        self.assertIsNone(trace.hop(9))

    def test_silent_hop_in_the_middle_is_kept(self) -> None:
        path = {1: _router(1), 3: _router(3), 4: TraceHopResult(HOP_TARGET, TARGET, 5.0)}
        trace = pt.trace_route(TARGET, max_hops=6, probe=_probe(path))

        self.assertEqual([hop.kind for hop in trace.hops], [HOP_ROUTER, HOP_SILENT, HOP_ROUTER, HOP_TARGET])

    def test_silent_hop_is_asked_again_and_answering_one_is_not(self) -> None:
        probe = _probe({1: _router(1), 3: TraceHopResult(HOP_TARGET, TARGET, 5.0)})
        pt.trace_route(TARGET, max_hops=3, attempts=2, probe=probe)

        self.assertEqual(sorted(probe.calls), [1, 2, 2, 3])

    def test_unreached_target_drops_silent_tail(self) -> None:
        trace = pt.trace_route(TARGET, max_hops=10, probe=_probe({1: _router(1), 2: _router(2)}))

        self.assertFalse(trace.reached)
        self.assertEqual([hop.ttl for hop in trace.hops], [1, 2])

    def test_unsupported_system_and_ipv6_are_reported_as_such(self) -> None:
        unsupported = pt.trace_route(TARGET, max_hops=3, probe=_probe({1: TraceHopResult(HOP_UNSUPPORTED)}))
        self.assertFalse(unsupported.supported)
        self.assertEqual(unsupported.hops, ())

        self.assertFalse(pt.trace_route("2001:db8::1", probe=_probe({})).supported)

    def test_stop_prevents_further_probes(self) -> None:
        probe = _probe({})
        pt.trace_route(TARGET, max_hops=4, should_stop=lambda: True, probe=probe)

        self.assertEqual(probe.calls, [])


def _network(filter_hop: int | None, *, server_alive: bool = True, stateful: bool = True, lose: set | None = None):
    """Учебная сеть для поиска фильтра: он стоит перед узлом ``filter_hop`` и запоминает соединение."""
    asked: list[int | None] = []
    lose = set(lose or ())

    def pair(ip, blocked_name, ttl, *, cancel):
        asked.append(ttl)
        if len(asked) in lose:
            return False
        if not server_alive:
            return False
        seen_by_filter = ttl is not None and filter_hop is not None and ttl >= filter_hop
        return not (seen_by_filter and stateful)

    pair.asked = asked
    return pair


class LocateFilterTests(unittest.TestCase):
    def test_filter_is_found_at_first_blocked_lifetime(self) -> None:
        pair = _network(6)
        facts = pt.locate_filter(TARGET, "rutracker.org", max_ttl=12, pair=pair)

        self.assertEqual((facts.control_ok, facts.stateful, facts.first_blocked_ttl), (True, True, 6))
        # Контроль, проверка «запоминает ли» (дважды), затем сроки 1–5 по разу и 6 дважды.
        self.assertEqual(pair.asked, [None, 128, 128, 1, 2, 3, 4, 5, 6, 6])

    def test_one_lost_packet_is_not_taken_for_the_filter(self) -> None:
        # Потерян ответ на пакет со сроком жизни 3: повтор проходит, поиск идёт дальше.
        pair = _network(6, lose={6})
        facts = pt.locate_filter(TARGET, "rutracker.org", max_ttl=12, pair=pair)

        self.assertEqual(facts.first_blocked_ttl, 6)
        self.assertEqual(pair.asked[3:8], [1, 2, 3, 3, 4])

    def test_no_search_without_working_control(self) -> None:
        pair = _network(6, server_alive=False)
        facts = pt.locate_filter(TARGET, "github.com", pair=pair)

        self.assertEqual((facts.control_ok, facts.first_blocked_ttl), (False, None))
        self.assertEqual(pair.asked, [None, None])

    def test_no_search_when_filter_does_not_remember_the_connection(self) -> None:
        pair = _network(6, stateful=False)
        facts = pt.locate_filter(TARGET, "www.youtube.com", pair=pair)

        self.assertEqual((facts.control_ok, facts.stateful, facts.first_blocked_ttl), (True, False, None))
        self.assertEqual(pair.asked, [None, 128])

    def test_filter_farther_than_search_limit_is_not_found(self) -> None:
        facts = pt.locate_filter(TARGET, "rutracker.org", max_ttl=4, pair=_network(9))

        self.assertEqual((facts.first_blocked_ttl, facts.checked_up_to), (None, 4))

    def test_cancel_stops_the_search_without_a_result(self) -> None:
        answers = iter([True, False, False, True, None])
        facts = pt.locate_filter(TARGET, "rutracker.org", pair=lambda *a, **k: next(answers))

        self.assertTrue(facts.cancelled)
        self.assertIsNone(pt.judge_filter(facts))


class JudgeFilterTests(unittest.TestCase):
    TRACE = pt.RouteTrace(
        target=TARGET,
        hops=tuple(pt.Hop(ttl, HOP_ROUTER, f"10.0.0.{ttl}", 1.0) for ttl in range(1, 8)),
        reached=True,
    )

    def test_found_filter_names_both_neighbouring_hops(self) -> None:
        verdict = pt.judge_filter(pt.FilterFacts(True, True, 6, 6), self.TRACE)

        self.assertEqual((verdict.code, verdict.hop), (pt.FILTER_FOUND, 6))
        self.assertEqual(verdict.text, "фильтр стоит между узлом 5 (10.0.0.5) и узлом 6 (10.0.0.6)")

    def test_hops_are_numbered_when_addresses_are_unknown(self) -> None:
        self.assertEqual(
            pt.judge_filter(pt.FilterFacts(True, True, 6, 6)).text, "фильтр стоит между узлом 5 и узлом 6"
        )
        silent = pt.RouteTrace(TARGET, (pt.Hop(5, HOP_SILENT), pt.Hop(6, HOP_ROUTER, "10.0.0.6")))
        self.assertEqual(
            pt.judge_filter(pt.FilterFacts(True, True, 6, 6), silent).text,
            "фильтр стоит между узлом 5 и узлом 6 (10.0.0.6)",
        )

    def test_filter_before_first_hop_points_at_home(self) -> None:
        verdict = pt.judge_filter(pt.FilterFacts(True, True, 1, 1), self.TRACE)

        self.assertEqual(verdict.hop, 1)
        self.assertIn("ваш роутер или программа на самом компьютере", verdict.text)

    def test_other_outcomes_are_explained_and_have_no_hop(self) -> None:
        cases = {
            pt.FILTER_NO_CONTROL: pt.FilterFacts(control_ok=False),
            pt.FILTER_NOT_STATEFUL: pt.FilterFacts(control_ok=True, stateful=False),
            pt.FILTER_NOT_ON_PATH: pt.FilterFacts(True, True, None, 20),
        }
        for code, facts in cases.items():
            with self.subTest(code=code):
                verdict = pt.judge_filter(facts, self.TRACE)
                self.assertEqual((verdict.code, verdict.hop), (code, None))
        self.assertIn("на первых 20 узлах", pt.judge_filter(cases[pt.FILTER_NOT_ON_PATH]).text)


class SendPairTests(unittest.TestCase):
    """Пара пакетов на учебный сервер: порядок, один сокет, срок жизни."""

    def _server(self, reply_to_second_only_if_alone: bool):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(3)
        self.addCleanup(sock.close)
        seen: list[tuple[tuple, bytes]] = []

        def serve() -> None:
            try:
                while True:
                    data, address = sock.recvfrom(4096)
                    seen.append((address, data))
                    # «Фильтр»: второй пакет с того же адреса остаётся без ответа.
                    earlier = sum(1 for item in seen if item[0] == address)
                    if reply_to_second_only_if_alone and earlier > 1:
                        continue
                    source_id = data[15:23]
                    sock.sendto(bytes([0xC0]) + struct.pack("!I", 1) + bytes([8]) + source_id + bytes(30), address)
            except OSError:
                pass

        threading.Thread(target=serve, daemon=True).start()
        return sock.getsockname()[1], seen

    def test_single_ordinary_packet_is_answered(self) -> None:
        port, seen = self._server(True)

        self.assertTrue(pt.send_pair("127.0.0.1", "blocked.example", None, port=port, reply_timeout=1.0))
        self.assertEqual(len(seen), 1)

    def test_both_packets_leave_from_one_socket_blocked_first(self) -> None:
        port, seen = self._server(True)

        answered = pt.send_pair("127.0.0.1", "blocked.example", 64, port=port, reply_timeout=0.4, settle=0.05)

        self.assertFalse(answered)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][0], seen[1][0], "оба пакета должны уйти с одного адреса и порта")
        # Это два разных соединения QUIC: номера у них свои.
        self.assertNotEqual(seen[0][1][6:14], seen[1][1][6:14])

    def test_answer_to_the_blocked_packet_is_not_taken_for_answer_to_ordinary(self) -> None:
        port, _seen = self._server(False)  # сервер отвечает на оба пакета

        self.assertTrue(pt.send_pair("127.0.0.1", "blocked.example", 64, port=port, reply_timeout=1.0, settle=0.05))

    def test_cancelled_probe_gives_no_answer(self) -> None:
        cancel = SocketCancel()
        cancel.cancel()

        self.assertIsNone(pt.send_pair("127.0.0.1", "blocked.example", None, port=9, cancel=cancel))


if __name__ == "__main__":
    unittest.main()
