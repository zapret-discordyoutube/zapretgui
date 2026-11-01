"""Приветствие «как у Chrome»: состав, отправка и вывод из сравнения с обычным."""

from __future__ import annotations

import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import diagnostics.engine as engine
from blockcheck_words import report_with_words
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

    def _flight(self, reply, **kwargs):
        server = _Server(reply, **kwargs)
        self.addCleanup(server.done.set)
        return browser_hello.send_parts("127.0.0.1", (browser_hello.build_hello("x.com", tls12=True),), port=server.port, timeout=0.6, flight=True)

    def test_whole_tls12_reply_is_read_up_to_server_hello_done(self) -> None:
        # ServerHello (4 байта тела), сертификат (3 байта) и ServerHelloDone — в двух записях.
        first = b"\x16\x03\x03\x00\x0b" + b"\x02\x00\x00\x01\x00" + b"\x0b\x00\x00\x02\x00\x00"
        second = b"\x16\x03\x03\x00\x04" + b"\x0e\x00\x00\x00"

        self.assertEqual(self._flight(first + second).kind, bc.HELLO_OK)

    def test_reply_cut_after_its_start_is_a_cut_not_an_answer(self) -> None:
        started = b"\x16\x03\x03\x00\x0b" + b"\x02\x00\x00\x01\x00" + b"\x0b\x00\x00\x02\x00\x00"

        closed = self._flight(started)
        silent = self._flight(started, close=False)

        self.assertEqual((closed.kind, closed.detail), (bc.HELLO_RESET, "ответ сервера оборвался после его начала"))
        self.assertEqual((silent.kind, silent.detail), (bc.HELLO_TIMEOUT, "ответ сервера оборвался после его начала"))
        # Без чтения ответа целиком то же начало ответа — обычное «сервер ответил».
        server = _Server(started)
        self.addCleanup(server.done.set)
        self.assertEqual(browser_hello.send_parts("127.0.0.1", (b"\x16\x03\x01\x00\x00",), port=server.port, timeout=0.6).kind, bc.HELLO_OK)

    def test_closed_port_is_no_connection(self) -> None:
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()

        self.assertEqual(browser_hello.send_hello("127.0.0.1", "x.com", port=port, timeout=0.6).kind, bc.HELLO_CONNECT)


def _facts(tls12=None, tls13=None):
    return pp.ProtocolFacts("x.com", "1.2.3.4", tls12, tls13)


def _main(kind, status=None):
    """Итог основной проверки — запроса «как Chrome» (``utils.chrome_https``)."""
    from diagnostics import tls_probe

    return tls_probe.ProbeResult(ip="1.2.3.4", kind=kind, status=status, elapsed_ms=50.0, cert_problem="чужой")


def _browser_line(facts, main):
    return next(line for line in pp.judge(facts, pp.chrome_line(main)) if line.key == pp.PROTO_BROWSER)


class BrowserLineTests(unittest.TestCase):
    """Строка «Как Chrome» — итог основной проверки; «почерк» — сравнение с простым приветствием."""

    def test_chrome_line_comes_from_the_main_request(self) -> None:
        from diagnostics import tls_probe

        opened = pp.chrome_line(_main(tls_probe.KIND_OK, 200))
        self.assertEqual((opened.state, opened.word), ("ok", "проходит"))
        cut = pp.chrome_line(_main(tls_probe.KIND_RESET))
        self.assertEqual((cut.state, cut.word), ("fail", pp.WORD_CUT))
        self.assertEqual(pp.chrome_line(_main(tls_probe.KIND_CERT)).word, "чужой сертификат")
        self.assertIsNone(pp.chrome_line(_main(tls_probe.KIND_CANCELLED)))

    def test_fingerprint_block_needs_the_plain_hello_to_pass(self) -> None:
        from diagnostics import tls_probe

        for failed in (tls_probe.KIND_RESET, tls_probe.KIND_TIMEOUT):
            with self.subTest(kind=failed):
                line = _browser_line(_facts(tls12=OK, tls13=OK), _main(failed))

                self.assertEqual((line.state, line.word, line.code), ("fail", pp.WORD_CUT, pp.CODE_FINGERPRINT))
                self.assertIn("почерк", line.text)

    def test_same_failure_as_the_plain_hello_is_not_a_fingerprint_block(self) -> None:
        from diagnostics import tls_probe

        line = _browser_line(_facts(tls12=RESET, tls13=RESET), _main(tls_probe.KIND_RESET))

        self.assertEqual((line.state, line.code), ("fail", ""))
        self.assertNotIn("почерк", line.text)

    def test_wrong_certificate_is_not_a_fingerprint_block(self) -> None:
        # Простое приветствие сертификат не проверяет: его «проходит» рядом с чужим сертификатом — не почерк.
        from diagnostics import tls_probe

        self.assertEqual(_browser_line(_facts(tls12=OK, tls13=OK), _main(tls_probe.KIND_CERT)).code, "")

    def test_open_site_keeps_only_the_chrome_line(self) -> None:
        from diagnostics import tls_probe

        lines = pp.judge(None, pp.chrome_line(_main(tls_probe.KIND_OK, 200)))
        self.assertEqual([line.key for line in lines], [pp.PROTO_BROWSER])

    def test_plain_hello_cut_stays_red(self) -> None:
        from diagnostics import tls_probe

        lines = {line.key: line for line in pp.judge(_facts(tls12=SILENT, tls13=SILENT), pp.chrome_line(_main(tls_probe.KIND_TIMEOUT)))}

        self.assertEqual((lines[pp.PROTO_TLS13].state, lines[pp.PROTO_TLS13].word), (pp.STATE_FAIL, "молчит"))
        # И красное слово объяснено: что случилось и кто это делает.
        self.assertIn("фильтр молча глушит", lines[pp.PROTO_TLS13].text)

    def test_every_road_has_a_plain_explanation(self) -> None:
        self.assertEqual(set(pp.HINTS), {pp.PROTO_TLS12, pp.PROTO_TLS13, pp.PROTO_BROWSER, pp.PROTO_HTTP})
        self.assertTrue(all(len(text) > 40 for text in pp.HINTS.values()))


class CollectTests(unittest.TestCase):
    def test_only_plain_hellos_and_http_are_sent(self) -> None:
        sent = []

        def _hello(ip, host, *, cancel, version):
            sent.append(version)
            return OK

        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(pp, "tls_hello", side_effect=_hello),
            patch.object(pp, "http_probe", return_value=bc.HttpFacts(status=200)),
            patch.object(browser_hello, "send_hello") as browser,
        ):
            facts = pp.collect("x.com", "1.2.3.4", submit=pool.submit, cancel=SocketCancel())

        # Браузерное приветствие отдельно не шлётся: основная проверка уже идёт как Chrome.
        browser.assert_not_called()
        self.assertEqual(sorted(sent), sorted([bc.TLS_1_2, bc.TLS_1_3]))
        self.assertEqual(facts.http.status, 200)


def _probe(host, lines, *, reach=ReachState.OK):
    probe = Probe(target=Target(host, "сайт"), service="x", host=host)
    probe.reach_state = reach
    probe.protocols = lines
    return probe


class FingerprintProblemTests(unittest.TestCase):
    def _cut_lines(self):
        from diagnostics import tls_probe

        return pp.judge(_facts(tls12=OK, tls13=OK), pp.chrome_line(_main(tls_probe.KIND_RESET)))

    def _problems(self, lines, *, control=False, reach=ReachState.DPI, zapret_running=True):
        service = Service("x", "X", (Target("x.com", "сайт", main=True),), control=control)
        probes = {"x": [_probe("x.com", lines, reach=reach)]}
        verdicts = {"x": engine._service_verdict(service, probes["x"], zapret_running=zapret_running)}
        found, _working, _spoofed = problems.collect_problems(
            {"x": service}, verdicts, probes, voice=None, freeze=None, zapret_running=zapret_running
        )
        return [item for item in found if item["kind"] == block_kind.KIND_FINGERPRINT]

    def test_site_cut_only_for_chrome_is_a_fingerprint_block_with_strategy_button(self) -> None:
        [item] = self._problems(self._cut_lines())

        self.assertEqual((item["level"], item["action"], item["target"]), ("fail", "strategy", "x.com"))
        self.assertIn("похожие на браузер", item["text"])

    def test_no_fingerprint_without_the_comparison(self) -> None:
        from diagnostics import tls_probe

        cut_everywhere = pp.judge(_facts(tls12=RESET, tls13=RESET), pp.chrome_line(_main(tls_probe.KIND_RESET)))
        self.assertEqual(self._problems(cut_everywhere), [])
        self.assertEqual(self._problems(self._cut_lines(), control=True), [])


class FingerprintOnScreenTests(unittest.TestCase):
    def test_every_block_kind_has_a_title_a_place_and_a_colour(self) -> None:
        self.assertEqual(set(block_kind.KIND_ORDER), set(block_kind.KINDS))
        for kind in block_kind.KINDS:
            self.assertIn(kind, _COLORS)
        self.assertEqual(Level.WARN.value, "warn")


if __name__ == "__main__":
    unittest.main()


class HelloVariantsTests(unittest.TestCase):
    def test_variants_drop_exactly_what_they_say(self) -> None:
        _c, without_ech, _n = browser_hello.parse_hello(browser_hello.build_hello("x.com", ech=False))
        self.assertNotIn(browser_hello.EXT_ECH, without_ech)
        self.assertEqual(len(without_ech), 15)

        compact = browser_hello.build_hello("x.com", post_quantum=False)
        # Без постквантового ключа приветствие умещается в один пакет.
        self.assertLess(len(compact), 1200)
        self.assertIn(browser_hello.EXT_ECH, browser_hello.parse_hello(compact)[1])
        self.assertNotEqual(browser_hello.ja4(browser_hello.build_hello("x.com", ech=False)), browser_hello.BLOCKED_JA4)
