from __future__ import annotations

import socket
import struct
import threading
import unittest
from unittest.mock import patch

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


def _locate(*args, distance: int | None = 30, **kwargs):
    """Поиск фильтра в учебной сети: расстояние до сервера задано, в настоящую сеть никто не ходит."""
    return pt.locate_filter(*args, distance_of=lambda _ip, cancel=None: distance, **kwargs)


def _network(
    filter_hop: int | None,
    *,
    server_alive: bool = True,
    stateful: bool = True,
    lose: set | None = None,
    by_name: bool = True,
):
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
        # ``by_name=False`` — поток гасит роутер при любом имени, а не фильтр по имени.
        if by_name and blocked_name == pt.NEUTRAL_NAME:
            return True
        return not (seen_by_filter and stateful)

    pair.asked = asked
    return pair


class LocateFilterTests(unittest.TestCase):
    def test_filter_is_found_at_first_blocked_lifetime(self) -> None:
        pair = _network(6)
        facts = _locate(TARGET, "rutracker.org", max_ttl=12, pair=pair)

        self.assertEqual((facts.control_ok, facts.stateful, facts.first_blocked_ttl), (True, True, 6))
        # Контроль, проверка «запоминает ли» (дважды), затем сроки 1–5 по разу и 6 дважды;
        # потом два подтверждения: безобидное имя на том же сроке и следующий узел дважды.
        self.assertEqual(pair.asked, [None, 128, 128, 1, 2, 3, 4, 5, 6, 6, 6, 7, 7])
        self.assertEqual((facts.neutral_passes, facts.next_blocked), (True, True))
        self.assertEqual(pt.judge_filter(facts).code, pt.FILTER_FOUND)

    def test_flow_dying_with_a_harmless_name_too_is_not_a_filter(self) -> None:
        """Роутер гасит поток, когда первый пакет не дошёл, каким бы ни было имя."""
        facts = _locate(TARGET, "rutracker.org", max_ttl=12, pair=_network(1, by_name=False))
        verdict = pt.judge_filter(facts)

        self.assertIs(facts.neutral_passes, False)
        self.assertEqual((verdict.code, verdict.hop), (pt.FILTER_UNSURE, None))
        self.assertIn("с безобидным именем", verdict.text)

    def test_two_losses_in_a_row_are_not_confirmed_by_the_next_hop(self) -> None:
        # Фильтра нет вовсе; на сроке жизни 3 оба пакета потерялись.
        pair = _network(None, lose={6, 7})
        # Проверка «запоминает ли» должна пройти, поэтому учебная сеть гасит только полный срок жизни.
        real = pair

        def lossy(ip, name, ttl, *, cancel):
            if ttl == 128:
                real.asked.append(ttl)
                return False
            return real(ip, name, ttl, cancel=cancel)

        facts = _locate(TARGET, "rutracker.org", max_ttl=12, pair=lossy)
        verdict = pt.judge_filter(facts)

        self.assertEqual((facts.first_blocked_ttl, facts.next_blocked), (3, False))
        self.assertEqual(verdict.code, pt.FILTER_UNSURE)
        self.assertIn("случайную потерю", verdict.text)

    def test_one_lost_packet_is_not_taken_for_the_filter(self) -> None:
        # Потерян ответ на пакет со сроком жизни 3: повтор проходит, поиск идёт дальше.
        pair = _network(6, lose={6})
        facts = _locate(TARGET, "rutracker.org", max_ttl=12, pair=pair)

        self.assertEqual(facts.first_blocked_ttl, 6)
        self.assertEqual(pair.asked[3:8], [1, 2, 3, 3, 4])

    def test_no_search_without_working_control(self) -> None:
        pair = _network(6, server_alive=False)
        facts = _locate(TARGET, "github.com", pair=pair)

        self.assertEqual((facts.control_ok, facts.first_blocked_ttl), (False, None))
        self.assertEqual(pair.asked, [None, None])

    def test_no_search_when_filter_does_not_remember_the_connection(self) -> None:
        pair = _network(6, stateful=False)
        facts = _locate(TARGET, "www.youtube.com", pair=pair)

        self.assertEqual((facts.control_ok, facts.stateful, facts.first_blocked_ttl), (True, False, None))
        self.assertEqual(pair.asked, [None, 128])

    def test_filter_farther_than_search_limit_is_not_found(self) -> None:
        facts = _locate(TARGET, "rutracker.org", max_ttl=4, pair=_network(9))

        self.assertEqual((facts.first_blocked_ttl, facts.checked_up_to), (None, 4))

    def test_cancel_stops_the_search_without_a_result(self) -> None:
        answers = iter([True, False, False, True, None])
        facts = _locate(TARGET, "rutracker.org", pair=lambda *a, **k: next(answers))

        self.assertTrue(facts.cancelled)
        self.assertIsNone(pt.judge_filter(facts))


class DistanceTests(unittest.TestCase):
    def test_search_never_reaches_the_server(self) -> None:
        # Фильтра на дороге нет, сервер на 5-м узле и сам гасит поток после запрещённого имени.
        pair = _network(5)
        facts = _locate(TARGET, "rutracker.org", max_ttl=20, pair=pair, distance=5)

        self.assertEqual((facts.first_blocked_ttl, facts.checked_up_to, facts.distance), (None, 4, 5))
        self.assertLessEqual(max(ttl for ttl in pair.asked if ttl not in (None, 128)), 4)
        self.assertEqual(pt.judge_filter(facts).code, pt.FILTER_AT_TARGET)

    def test_filter_right_before_the_server_is_confirmed_without_going_past_it(self) -> None:
        pair = _network(4)
        facts = _locate(TARGET, "rutracker.org", pair=pair, distance=5)

        self.assertEqual((facts.first_blocked_ttl, facts.next_blocked), (4, True))
        self.assertNotIn(5, pair.asked)
        self.assertEqual(pt.judge_filter(facts).hop, 4)

    def test_unknown_distance_means_no_search_at_all(self) -> None:
        pair = _network(3)
        facts = _locate(TARGET, "rutracker.org", pair=pair, distance=None)

        self.assertEqual((pair.asked, facts.distance), ([], None))
        self.assertEqual(pt.judge_filter(facts).code, pt.FILTER_NO_DISTANCE)

    def test_distance_is_the_smallest_lifetime_that_connects(self) -> None:
        def measure(reached_from: int, holes=()):
            with patch.object(pt, "_connects_with", lambda ip, ttl, *_a: ttl >= reached_from and ttl not in holes):
                return pt.tcp_distance(TARGET, max_hops=12)

        self.assertEqual(measure(7), 7)
        self.assertIsNone(measure(99))
        # Соединилось на 7, а на 8 нет — дорог несколько или пакеты теряются: расстоянию верить нельзя.
        self.assertIsNone(measure(7, holes=(8,)))
        self.assertIsNone(pt.tcp_distance("2001:db8::1"))

    def test_distance_with_a_hint_asks_only_the_neighbourhood(self) -> None:
        def measure(reached_from: int, around: int):
            asked: list[int] = []

            def connects(ip, ttl, *_a):
                asked.append(ttl)
                return ttl >= reached_from

            return pt.tcp_distance(TARGET, max_hops=30, around=around, connects=connects), sorted(asked)

        # Подсказка верна: семь соединений вокруг неё вместо тридцати.
        self.assertEqual(measure(12, around=12), (12, [9, 10, 11, 12, 13, 14, 15]))
        # Сервер ближе, чем подсказка: в окрестности соединились все, нижняя граница
        # не видна — идёт полный перебор, и он находит настоящее расстояние.
        distance, asked = measure(4, around=12)
        self.assertEqual(distance, 4)
        self.assertEqual(asked, list(range(1, 16)))

    def test_distance_search_stops_once_the_server_is_passed(self) -> None:
        asked: list[int] = []

        def connects(ip, ttl, *_a):
            asked.append(ttl)
            return ttl >= 6

        self.assertEqual(pt.tcp_distance(TARGET, max_hops=30, connects=connects), 6)
        self.assertEqual(max(asked), pt.DISTANCE_AT_ONCE)

    def test_trace_does_not_ask_hops_far_beyond_the_target(self) -> None:
        asked: list[int] = []

        def probe(ip, ttl, *, timeout_ms):
            asked.append(ttl)
            return TraceHopResult(HOP_TARGET, TARGET, 5.0) if ttl >= 3 else TraceHopResult(HOP_ROUTER, f"10.0.0.{ttl}", 1.0)

        trace = pt.trace_route(TARGET, max_hops=30, probe=probe)

        self.assertTrue(trace.reached)
        self.assertEqual(len(trace.hops), 3)
        # Первая волна уходит целиком, дальше неё спрашивать уже незачем.
        self.assertLessEqual(max(asked), pt.TRACE_AT_ONCE)


class _TcpServer:
    """Сервер на этом компьютере: принимает соединение и отвечает заданным образом."""

    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _addr = self.listener.accept()
        try:
            conn.settimeout(2)
            conn.recv(4096)
            if self.behaviour == "answer":
                conn.sendall(b"\x16\x03\x03\x00\x04")
                self.stop.wait(1)
            elif self.behaviour == "reset":
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            else:
                self.stop.wait(2)
        except OSError:
            pass
        finally:
            conn.close()
            self.listener.close()


class TcpPairTests(unittest.TestCase):
    def _pair(self, behaviour: str, ttl):
        server = _TcpServer(behaviour)
        self.addCleanup(server.stop.set)
        return pt.tcp_pair("127.0.0.1", "x.com", ttl, port=server.port, window=0.5)

    def test_control_is_alive_only_when_the_server_answers(self) -> None:
        self.assertIs(self._pair("answer", None), True)
        self.assertIs(self._pair("silent", None), False)
        self.assertIs(self._pair("reset", None), False)

    def test_probe_is_killed_only_by_a_reset(self) -> None:
        self.assertIs(self._pair("reset", 64), False)
        # Тишина — не сброс: тихий фильтр этим способом не ловится, и контроль «запоминает ли» это покажет.
        self.assertIs(self._pair("silent", 64), True)

    def test_silent_filter_gives_no_verdict_by_tcp(self) -> None:
        # Запрещённое имя с полным сроком жизни не вызывает сброса — способ неприменим.
        facts = _locate(TARGET, "x.com", pair=lambda ip, name, ttl, *, cancel: True, distance=9)

        self.assertEqual(pt.judge_filter(facts).code, pt.FILTER_NOT_STATEFUL)


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
            pt.FILTER_NOT_ON_PATH: pt.FilterFacts(True, True, None, 4),
            pt.FILTER_NO_DISTANCE: pt.FilterFacts(distance=None),
        }
        for code, facts in cases.items():
            with self.subTest(code=code):
                verdict = pt.judge_filter(facts, self.TRACE)
                self.assertEqual((verdict.code, verdict.hop), (code, None))
        self.assertIn("на первых 4 узлах", pt.judge_filter(cases[pt.FILTER_NOT_ON_PATH]).text)

    def test_filter_is_never_placed_at_or_beyond_the_server(self) -> None:
        """Случай из жизни: сервер на 17-м узле, а программа писала «фильтр между узлом 17 и 18»."""
        trace = pt.RouteTrace(
            target=TARGET,
            hops=tuple(pt.Hop(ttl, HOP_ROUTER, f"10.0.0.{ttl}", 1.0) for ttl in range(1, 17)) + (pt.Hop(17, "target", TARGET, 1.0),),
            reached=True,
        )
        for hop in (17, 18, 25):
            with self.subTest(hop=hop):
                facts = pt.FilterFacts(True, True, hop, hop, neutral_passes=True, next_blocked=True)
                verdict = pt.judge_filter(facts, trace)
                self.assertEqual((verdict.code, verdict.hop), (pt.FILTER_AT_TARGET, None))
                self.assertNotIn("между", verdict.text)
        # То же, когда расстояние известно по TCP, а пинг до сервера не дошёл.
        by_tcp = pt.FilterFacts(True, True, 9, 9, neutral_passes=True, next_blocked=True, distance=9)
        self.assertEqual(pt.judge_filter(by_tcp).code, pt.FILTER_AT_TARGET)
        self.assertEqual(pt.judge_filter(pt.FilterFacts(True, True, 8, 8, neutral_passes=True, next_blocked=True, distance=9)).hop, 8)

    def test_connection_accepted_at_the_first_hop_is_a_local_proxy_not_a_distance(self) -> None:
        verdict = pt.judge_filter(pt.FilterFacts(True, True, None, 0, distance=1))

        self.assertEqual(verdict.code, pt.FILTER_NO_DISTANCE)
        self.assertIn("прокси", verdict.text)


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
