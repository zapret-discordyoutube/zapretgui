"""«Как работает фильтр»: какое дробление приветствия проходит и режется ли ECH."""

from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import browser_hello
from diagnostics import filter_habits as fh

HOST = "blocked.example"
IP = "45.1.2.3"


class _Filter:
    """Учебный фильтр: режет приветствие, если видит запрещённое имя тем способом, каким умеет читать."""

    def __init__(self, *, joins_packets=False, joins_records=False, server_takes_split=True, blocked=(HOST,)) -> None:
        self.joins_packets = joins_packets
        self.joins_records = joins_records
        self.server_takes_split = server_takes_split
        self.blocked = tuple(name.encode() for name in blocked)
        self.sent: list[tuple[int, float]] = []

    def _sees(self, parts: tuple[bytes, ...]) -> bool:
        packets = [b"".join(parts)] if self.joins_packets else list(parts)
        views: list[bytes] = []
        for packet in packets:
            records, offset = [], 0
            while offset + 5 <= len(packet) and packet[offset] == 22:
                size = int.from_bytes(packet[offset + 3 : offset + 5], "big")
                records.append(packet[offset + 5 : offset + 5 + size])
                offset += 5 + size
            if not records:
                # Хвост записи без заголовка: фильтр видит его как есть.
                views.append(packet)
            elif self.joins_records:
                views.append(b"".join(records))
            else:
                views.extend(records)
        return any(name in view for view in views for name in self.blocked)

    def __call__(self, ip: str, parts: tuple[bytes, ...], pause: float) -> str:
        self.sent.append((len(parts), pause))
        if self._sees(parts):
            return "timeout"
        split = len(parts) > 1 or parts[0].count(b"\x16\x03\x01") > 1
        if split and not self.server_takes_split:
            return "timeout"
        return "ok"


def _verdict(network) -> fh.SplitVerdict:
    with ThreadPoolExecutor(8) as pool:
        return fh.judge_split(fh.check_split(HOST, IP, send=network, submit=pool.submit))


class PartsTests(unittest.TestCase):
    def test_every_way_cuts_the_name_in_the_middle(self) -> None:
        tcp, pause = fh._parts(HOST, fh.WAY_TCP)
        self.assertEqual(len(tcp), 2)
        self.assertGreater(pause, 0)
        self.assertNotIn(HOST.encode(), tcp[0])
        self.assertNotIn(HOST.encode(), tcp[1])
        self.assertIn(HOST.encode(), b"".join(tcp))

        (records,), pause = fh._parts(HOST, fh.WAY_RECORDS)
        self.assertEqual((pause, records.count(b"\x16\x03\x01") >= 2), (0.0, True))
        self.assertNotIn(HOST.encode(), records)

        both, _pause = fh._parts(HOST, fh.WAY_BOTH)
        self.assertEqual([part[0] for part in both], [22, 22])

    def test_two_records_carry_the_same_hello(self) -> None:
        record = browser_hello.build_hello(HOST, post_quantum=False)
        first, second = browser_hello.split_record(record, browser_hello.name_offset(record, HOST) + 3)

        self.assertEqual(first[5:] + second[5:], record[5:])
        self.assertEqual(int.from_bytes(first[3:5], "big"), len(first) - 5)
        self.assertLess(len(record), 1200)


class SplitJudgeTests(unittest.TestCase):
    def test_filter_reading_single_packets_is_beaten_by_any_split(self) -> None:
        verdict = _verdict(_Filter())

        self.assertEqual(verdict.code, fh.HABIT_ALL)
        self.assertEqual(dict(verdict.ways), {"whole": "cut", "tcp": "passed", "records": "passed", "both": "passed"})
        self.assertIn("не склеивает пакеты", verdict.text)
        self.assertIn("split", verdict.advice)

    def test_filter_joining_packets_but_not_records_needs_record_split(self) -> None:
        verdict = _verdict(_Filter(joins_packets=True))

        self.assertEqual((verdict.code, dict(verdict.ways)["tcp"], dict(verdict.ways)["records"]), (fh.HABIT_SOME, "cut", "passed"))
        self.assertIn("tlsrec", verdict.advice)

    def test_filter_joining_records_but_not_packets_is_beaten_by_packet_split(self) -> None:
        verdict = _verdict(_Filter(joins_records=True))

        self.assertEqual((dict(verdict.ways)["tcp"], dict(verdict.ways)["records"], dict(verdict.ways)["both"]), ("passed", "cut", "passed"))
        self.assertIn("split", verdict.advice)

    def test_filter_joining_everything_makes_splitting_useless(self) -> None:
        verdict = _verdict(_Filter(joins_packets=True, joins_records=True))

        self.assertEqual(verdict.code, fh.HABIT_NONE)
        self.assertIn("fake", verdict.advice)

    def test_server_refusing_split_hello_is_not_blamed_on_the_filter(self) -> None:
        verdict = _verdict(_Filter(server_takes_split=False))

        self.assertEqual(verdict.code, fh.HABIT_UNKNOWN)
        self.assertEqual(dict(verdict.ways)["tcp"], "no_control")
        self.assertEqual(verdict.advice, "")

    def test_whole_hello_passing_means_no_block_by_name_here(self) -> None:
        verdict = _verdict(_Filter(blocked=()))

        self.assertEqual((verdict.code, verdict.advice), (fh.HABIT_NOT_BY_NAME, ""))

    def test_result_needs_both_repeats_to_agree(self) -> None:
        calls = {"n": 0}
        strict = _Filter()

        def flaky(ip, parts, pause):
            # Разрез TCP с настоящим именем проходит через раз.
            if len(parts) == 2 and parts[0][0] == 22 and parts[1][0] != 22 and HOST[:3].encode() in b"".join(parts):
                calls["n"] += 1
                return "ok" if calls["n"] % 2 else "reset"
            return strict(ip, parts, pause)

        verdict = _verdict(flaky)
        self.assertEqual(dict(verdict.ways)["tcp"], "unstable")

    def test_cancelled_probe_gives_no_verdict(self) -> None:
        self.assertIsNone(_verdict(lambda *_a: "cancelled"))


class EchTests(unittest.TestCase):
    def _judge(self, send):
        return fh.judge_ech(fh.check_ech(IP, send=send))

    def test_only_ech_with_cloudflare_outer_name_being_cut_is_an_ech_block(self) -> None:
        def send(ip, parts, pause):
            _c, extensions, name = browser_hello.parse_hello(parts[0])
            return "timeout" if name == fh.ECH_OUTER and browser_hello.EXT_ECH in extensions else "ok"

        code, text = self._judge(send)
        self.assertEqual(code, fh.ECH_BLOCKED)
        self.assertIn("без ECH проходят", text)

    def test_name_cut_with_and_without_ech_is_not_an_ech_block(self) -> None:
        def send(ip, parts, pause):
            return "timeout" if browser_hello.parse_hello(parts[0])[2] == fh.ECH_OUTER else "ok"

        self.assertEqual(self._judge(send)[0], fh.ECH_UNKNOWN)

    def test_everything_passing_is_fine_and_flaky_is_unknown(self) -> None:
        self.assertEqual(self._judge(lambda *_a: "ok")[0], fh.ECH_FINE)
        self.assertEqual(self._judge(lambda *_a: "alert")[0], fh.ECH_FINE)
        answers = iter(["ok", "ok", "timeout", "ok"])
        self.assertEqual(self._judge(lambda *_a: next(answers))[0], fh.ECH_UNKNOWN)
        self.assertEqual(fh.judge_ech(None), (fh.ECH_UNKNOWN, ""))


class ReportTests(unittest.TestCase):
    def _report(self, *filters, ech=(fh.ECH_FINE, "соединения с ECH проходят")):
        return fh.summarize([_verdict(item) for item in filters], ech)

    def test_same_habit_on_every_site_becomes_the_headline_with_advice(self) -> None:
        report = self._report(_Filter(joins_packets=True), _Filter(joins_packets=True))

        self.assertIn("записи шифрования — нет", report["headline"])
        self.assertIn("tlsrec", report["advice"])
        text = "\n".join(fh.lines(report))
        self.assertIn("❌ Разрез TCP посреди имени: режется", text)
        self.assertIn("✅ Две записи TLS: проходит", text)

    def test_different_habits_are_not_merged_into_one_advice(self) -> None:
        report = self._report(_Filter(), _Filter(joins_packets=True, joins_records=True))

        self.assertIn("по-разному", report["headline"])
        self.assertEqual(report["advice"], "")

    def test_nothing_to_show_gives_no_section(self) -> None:
        self.assertIsNone(fh.summarize([], (fh.ECH_UNKNOWN, "")))

    def test_card_shows_ways_and_warns_about_blocked_ech(self) -> None:
        report = self._report(_Filter(), ech=(fh.ECH_BLOCKED, "соединения с ECH режутся"))
        [card] = build_cards({"habits": report})

        self.assertEqual((card.key, card.level, card.status), ("habits", "warn", "ECH режется"))
        rows = {line.name: (line.state, line.text) for line in card.sections[0].lines}
        self.assertEqual(rows["Целиком"], ("fail", "режется"))
        self.assertEqual(rows["Разрез TCP посреди имени"], ("ok", "проходит"))
        self.assertIn("chrome://flags", card.sections[-1].lines[-1].text)
        self.assertIn("Chrome", report["ech"]["advice"])


if __name__ == "__main__":
    unittest.main()
