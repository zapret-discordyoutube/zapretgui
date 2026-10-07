"""Приветствие «как у Chrome»: состав, отправка и вывод из сравнения с обычным."""

from __future__ import annotations

import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import diagnostics.engine as engine
from blockcheck.ui.block_kinds_view import _COLORS
from blockcheck.ui.result_cards_model import build_cards
from diagnostics import block_cause as bc
from diagnostics import block_kind, browser_hello, problems
from diagnostics import protocol_probe as pp
from diagnostics.run_context import Probe
from diagnostics.services import Service, Target
from diagnostics.verdict import Level, ReachState
from utils.socket_cancel import SocketCancel

OK = bc.HelloResult(bc.HELLO_OK, ms=40.0)
RESET = bc.HelloResult(bc.HELLO_RESET)
SILENT = bc.HelloResult(bc.HELLO_TIMEOUT)


class HelloCompositionTests(unittest.TestCase):
    def test_hello_has_the_ciphers_and_extensions_of_chrome(self) -> None:
        ciphers, extensions, name = browser_hello.parse_hello(browser_hello.build_hello("x.com"))

        self.assertEqual(ciphers, browser_hello.CHROME_CIPHERS)
        self.assertEqual(sorted(extensions), sorted(browser_hello.CHROME_EXTENSIONS))
        self.assertEqual(name, "x.com")

    def test_fingerprint_is_the_one_the_filter_is_known_to_cut(self) -> None:
        """Иначе строка «Как Chrome» проверяла бы почерк, на который фильтр не смотрит."""
        for _ in range(4):
            self.assertEqual(browser_hello.ja4(browser_hello.build_hello("x.com")), browser_hello.BLOCKED_JA4)

    def test_hello_takes_two_packets_like_a_real_browser(self) -> None:
        record = browser_hello.build_hello("x.com")

        self.assertGreater(len(record), 1500)
        # Заголовок записи: рукопожатие, длина совпадает с содержимым.
        self.assertEqual(record[0], 22)
        self.assertEqual(int.from_bytes(record[3:5], "big"), len(record) - 5)

    def test_extension_order_changes_between_connections(self) -> None:
        orders = {browser_hello.parse_hello(browser_hello.build_hello("x.com"))[1] for _ in range(8)}

        self.assertGreater(len(orders), 1)

    def test_post_quantum_key_would_pass_the_server_check(self) -> None:
        """Сервер отвергает ключ, в котором хоть одно число не меньше 3329."""
        key = browser_hello._mlkem_public_key(lambda size: b"\xff" * size)

        self.assertEqual(len(key), 1184)
        packed = key[:1152]
        for offset in range(0, len(packed), 3):
            first = packed[offset] | ((packed[offset + 1] & 0x0F) << 8)
            second = (packed[offset + 1] >> 4) | (packed[offset + 2] << 4)
            self.assertLess(first, 3329)
            self.assertLess(second, 3329)


class _Server:
    """Сервер на этом компьютере: читает приветствие и отвечает заданным образом."""

    def __init__(self, reply: bytes | None, *, close: bool = True) -> None:
        self.reply = reply
        self.close = close
        self.received = bytearray()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _addr = self.listener.accept()
        conn.settimeout(2)
        try:
            while len(self.received) < 5 or len(self.received) < 5 + int.from_bytes(self.received[3:5], "big"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.received += chunk
            if self.reply is not None:
                conn.sendall(self.reply)
            if not self.close:
                self.done.wait(3)
        finally:
            conn.close()
            self.listener.close()


class SendHelloTests(unittest.TestCase):
    def _send(self, reply, **kwargs):
        server = _Server(reply, **kwargs)
        self.addCleanup(server.done.set)
        result = browser_hello.send_hello("127.0.0.1", "x.com", port=server.port, timeout=0.6)
        return result, server

    def test_server_hello_means_the_hello_got_through(self) -> None:
        result, server = self._send(b"\x16\x03\x03\x00\x04\x02\x00\x00\x00")

        self.assertEqual(result.kind, bc.HELLO_OK)
        self.assertIsNotNone(result.ms)
        # Сервер получил приветствие целиком и с нужным именем.
        self.assertEqual(browser_hello.parse_hello(bytes(server.received))[2], "x.com")

    def test_refusal_from_the_server_is_still_an_answer(self) -> None:
        result, _server = self._send(b"\x15\x03\x03\x00\x02\x02\x2f")

        self.assertEqual(result.kind, bc.HELLO_ALERT)

    def test_silence_and_closed_connection_are_told_apart(self) -> None:
        self.assertEqual(self._send(None, close=False)[0].kind, bc.HELLO_TIMEOUT)
        self.assertEqual(self._send(None)[0].kind, bc.HELLO_RESET)

    def test_something_that_is_not_tls_is_a_foreign_answer(self) -> None:
        self.assertEqual(self._send(b"HTTP/1.1 403 Forbidden\r\n\r\n")[0].kind, bc.HELLO_GARBAGE)

    def test_closed_port_is_no_connection(self) -> None:
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()

        self.assertEqual(browser_hello.send_hello("127.0.0.1", "x.com", port=port, timeout=0.6).kind, bc.HELLO_CONNECT)


def _facts(tls12=None, tls13=None, browser=None):
    return pp.ProtocolFacts("x.com", "1.2.3.4", tls12, tls13, browser=browser)


def _browser_line(facts):
    return next(line for line in pp.judge(facts) if line.key == pp.PROTO_BROWSER)


class BrowserLineTests(unittest.TestCase):
    def test_fingerprint_block_needs_the_plain_hello_to_pass(self) -> None:
        for failed in (RESET, SILENT):
            with self.subTest(kind=failed.kind):
                line = _browser_line(_facts(tls12=OK, tls13=OK, browser=failed))

                self.assertEqual((line.state, line.word, line.code), ("fail", "режется", pp.CODE_FINGERPRINT))
                self.assertIn("почерк", line.text)

    def test_same_failure_as_the_plain_hello_is_not_a_fingerprint_block(self) -> None:
        line = _browser_line(_facts(tls12=RESET, tls13=RESET, browser=RESET))

        self.assertEqual((line.state, line.word, line.code), ("fail", "сброс", ""))
        self.assertNotIn("почерк", line.text)

    def test_without_a_plain_hello_there_is_nothing_to_compare_with(self) -> None:
        self.assertEqual(_browser_line(_facts(browser=SILENT)).code, "")

    def test_passing_hello_is_ok_and_refusal_counts_as_passing(self) -> None:
        for answer in (OK, bc.HelloResult(bc.HELLO_ALERT, ms=30.0)):
            line = _browser_line(_facts(tls12=OK, tls13=OK, browser=answer))
            self.assertEqual((line.state, line.word, line.code), ("ok", "проходит", ""))

    def test_browser_hello_passing_where_plain_is_cut_is_said_so(self) -> None:
        line = _browser_line(_facts(tls12=RESET, tls13=SILENT, browser=OK))

        self.assertEqual((line.state, line.code), ("ok", pp.CODE_BROWSER_ONLY))
        self.assertIn("в самом браузере сайт, возможно, открывается", line.text)

    def test_no_connection_and_cancel_give_no_verdict(self) -> None:
        self.assertEqual(_browser_line(_facts(tls12=OK, browser=bc.HelloResult(bc.HELLO_CONNECT))).state, "unknown")
        self.assertEqual([line.key for line in pp.judge(_facts(tls12=OK, browser=bc.HelloResult(bc.HELLO_CANCELLED)))], ["tls12"])


class CollectTests(unittest.TestCase):
    def _collect(self, answers):
        sent = []

        def _send(ip, host, *, cancel):
            sent.append((ip, host))
            return answers[min(len(sent), len(answers)) - 1]

        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(pp, "tls_hello", return_value=OK),
            patch.object(pp, "http_probe", return_value=bc.HttpFacts(status=200)),
            patch.object(pp.browser_hello, "send_hello", side_effect=_send),
            patch.object(pp, "BROWSER_RETRY_PAUSE_S", 0.0),
        ):
            facts = pp.collect("x.com", "1.2.3.4", submit=pool.submit, cancel=SocketCancel())
        return facts, sent

    def test_passing_hello_is_sent_once(self) -> None:
        facts, sent = self._collect([OK])

        self.assertEqual((facts.browser.kind, sent), (bc.HELLO_OK, [("1.2.3.4", "x.com")]))

    def test_single_failure_is_rechecked_and_does_not_count(self) -> None:
        facts, sent = self._collect([SILENT, OK])

        self.assertEqual((facts.browser.kind, len(sent)), (bc.HELLO_OK, 2))
        self.assertEqual(_browser_line(facts).code, "")

    def test_failure_twice_in_a_row_is_the_finding(self) -> None:
        facts, sent = self._collect([RESET, RESET])

        self.assertEqual(len(sent), 2)
        self.assertEqual(_browser_line(facts).code, pp.CODE_FINGERPRINT)


def _probe(host, lines, *, reach=ReachState.OK):
    probe = Probe(target=Target(host, "сайт"), service="x", host=host)
    probe.reach_state = reach
    probe.protocols = lines
    return probe


class FingerprintProblemTests(unittest.TestCase):
    CUT = pp.judge(_facts(tls12=OK, tls13=OK, browser=RESET))
    FINE = pp.judge(_facts(tls12=OK, tls13=OK, browser=OK))

    def _problems(self, lines, *, control=False, reach=ReachState.OK, zapret_running=True):
        service = Service("x", "X", (Target("x.com", "сайт"),), control=control)
        probes = {"x": [_probe("x.com", lines, reach=reach)]}
        verdicts = {"x": engine._service_verdict(service, probes["x"], zapret_running=zapret_running)}
        found, _working, _spoofed = problems.collect_problems(
            {"x": service}, verdicts, probes, voice=None, freeze=None, zapret_running=zapret_running
        )
        return [item for item in found if item["kind"] == block_kind.KIND_FINGERPRINT]

    def test_open_site_cut_for_chrome_becomes_a_warning_with_strategy_button(self) -> None:
        [item] = self._problems(self.CUT)

        self.assertEqual((item["level"], item["action"], item["target"], item["title"]), ("warn", "strategy", "x.com", "X"))
        self.assertIn("в браузере он может не открываться", item["text"])
        self.assertIn("проверено дважды", item["evidence"][0])
        self.assertEqual(self._problems(self.CUT, zapret_running=False)[0]["action"], "start_zapret")

    def test_no_warning_when_chrome_hello_passes_or_site_is_down_anyway(self) -> None:
        self.assertEqual(self._problems(self.FINE), [])
        # Сайт и так не открылся: о нём уже есть своя строка, вторая была бы шумом.
        self.assertEqual(self._problems(self.CUT, reach=ReachState.DPI), [])
        self.assertEqual(self._problems(self.CUT, control=True), [])


class FingerprintOnScreenTests(unittest.TestCase):
    def _card(self, code):
        report = {
            "services": [
                {
                    "key": "x",
                    "label": "X",
                    "level": "ok",
                    "targets": [
                        {
                            "host": "x.com",
                            "main": True,
                            "ok": True,
                            "protocols": [
                                {"key": "browser", "title": "Как Chrome", "state": "fail", "word": "режется",
                                 "text": "не проходит", "code": code}
                            ],
                        }
                    ],
                }
            ]
        }
        return build_cards(report)[0]

    def test_card_of_an_open_site_turns_to_warning(self) -> None:
        card = self._card("fingerprint")

        self.assertEqual((card.level, card.status), ("warn", "Есть проблемы"))
        self.assertIn(("Как Chrome: режется", "fail"), card.chips)
        self.assertEqual(self._card("").level, "ok")

    def test_every_block_kind_has_a_title_a_place_and_a_colour(self) -> None:
        self.assertEqual(set(block_kind.KIND_ORDER), set(block_kind.KINDS))
        for kind in block_kind.KINDS:
            self.assertIn(kind, _COLORS)
        self.assertEqual(Level.WARN.value, "warn")


if __name__ == "__main__":
    unittest.main()
