"""UDP по числу пакетов: поток, который замирает после первых двух десятков."""

from __future__ import annotations

import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import udp_burst as ub

FULL = (True,) * ub.PACKETS


def _cut(at: int) -> tuple[bool, ...]:
    return (True,) * at + (False,) * (ub.PACKETS - at)


def _facts(google, cloudflare):
    return (("Google", tuple(google)), ("Cloudflare", tuple(cloudflare)))


class JudgeTests(unittest.TestCase):
    def test_whole_series_answered_means_no_freeze(self) -> None:
        verdict = ub.judge(_facts([FULL, FULL], [FULL, FULL]))

        self.assertEqual(verdict.code, ub.BURST_OK)
        self.assertEqual(verdict.servers[0], ("Google", ("30 из 30", "30 из 30")))

    def test_two_lost_packets_are_still_a_whole_series(self) -> None:
        lossy = tuple(index not in (7, 19) for index in range(ub.PACKETS))
        self.assertEqual(ub.judge(_facts([lossy, FULL], [FULL, FULL])).code, ub.BURST_OK)

    def test_same_cut_on_both_servers_and_both_repeats_is_a_freeze(self) -> None:
        verdict = ub.judge(_facts([_cut(25), _cut(24)], [_cut(25), _cut(26)]))

        self.assertEqual(verdict.code, ub.BURST_FREEZE)
        self.assertIn("после 25 пакетов", verdict.text)

    def test_cut_on_one_server_only_is_that_servers_limit_not_a_filter(self) -> None:
        verdict = ub.judge(_facts([_cut(25), _cut(25)], [FULL, FULL]))

        self.assertEqual(verdict.code, ub.BURST_UNKNOWN)
        self.assertIn("ограничение самого сервера", verdict.text)

    def test_cut_that_does_not_repeat_or_lands_elsewhere_is_not_a_freeze(self) -> None:
        self.assertEqual(ub.judge(_facts([_cut(25), FULL], [_cut(25), _cut(25)])).code, ub.BURST_UNKNOWN)
        self.assertEqual(ub.judge(_facts([_cut(12), _cut(12)], [_cut(25), _cut(25)])).code, ub.BURST_UNKNOWN)
        # Обрыв в самом начале или на последнем пакете — не «начало проходит, дальше тишина».
        self.assertIsNone(ub._cut_at(_cut(3)))
        self.assertIsNone(ub._cut_at(_cut(ub.PACKETS - 1)))

    def test_nothing_answered_gives_no_verdict_here(self) -> None:
        silent = (False,) * ub.PACKETS
        self.assertIsNone(ub.judge(_facts([None, None], [silent, None])))


class _Stun:
    """Сервер на этом компьютере: отвечает на первые ``limit`` запросов, дальше молчит."""

    def __init__(self, limit: int) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(2)
        self.port = self.sock.getsockname()[1]
        self.limit = limit
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        seen = 0
        try:
            while True:
                data, peer = self.sock.recvfrom(2048)
                seen += 1
                if seen <= self.limit:
                    self.sock.sendto(b"\x01\x01\x00\x00" + data[4:20], peer)
        except OSError:
            pass
        finally:
            self.sock.close()


class SendTests(unittest.TestCase):
    def _burst(self, limit: int):
        server = _Stun(limit)
        return ub.send_burst("127.0.0.1", server.port, gap=0.002, tail_wait=0.3)

    def test_every_answer_is_matched_to_its_request(self) -> None:
        self.assertEqual(self._burst(999), FULL)

    def test_stream_that_goes_silent_is_seen_as_a_cut(self) -> None:
        run = self._burst(25)

        self.assertEqual((sum(run), ub._cut_at(run)), (25, 25))

    def test_unreachable_server_is_not_an_error(self) -> None:
        free = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        run = ub.send_burst("127.0.0.1", port, gap=0.001, tail_wait=0.1)

        self.assertTrue(run is None or not any(run))

    def test_servers_are_asked_twice_each(self) -> None:
        asked: list[str] = []

        def send(host, port):
            asked.append(host)
            return FULL

        with ThreadPoolExecutor(4) as pool:
            facts = ub.check_bursts(send, submit=pool.submit)

        self.assertEqual(sorted(asked), sorted([host for _n, host, _p in ub.SERVERS] * ub.REPEATS))
        self.assertEqual(ub.judge(facts).code, ub.BURST_OK)


class CardTests(unittest.TestCase):
    def _card(self, state, text):
        voice = {"level": "ok", "headline": "Серверы отвечают", "items": [{"name": "Google STUN", "state": "ok", "ok": True, "text": "отвечает"}],
                 "burst": {"state": state, "text": text, "servers": [{"name": "Google", "series": ["25 из 30", "25 из 30"]}]}}
        return build_cards({"voice": voice})[0]

    def test_frozen_udp_turns_the_voice_card_into_a_warning(self) -> None:
        card = self._card("freeze", "ответы прекращаются после 25 пакетов")

        self.assertEqual(card.level, "warn")
        self.assertEqual(card.lines[-1].name, "UDP замирает")
        rows = card.sections[1].lines
        self.assertEqual((rows[0].state, rows[1].text), ("warn", "ответов: 25 из 30, 25 из 30"))

    def test_passing_series_is_shown_without_alarm(self) -> None:
        card = self._card("ok", "серия проходит целиком")

        self.assertEqual((card.level, card.sections[1].lines[0].state), ("ok", "ok"))


if __name__ == "__main__":
    unittest.main()
