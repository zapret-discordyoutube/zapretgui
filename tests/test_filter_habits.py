"""«Как работает фильтр»: что фильтр делает с началом соединения и режется ли ECH."""

from __future__ import annotations

import unittest

from blockcheck.ui.result_cards_model import build_cards
from diagnostics import browser_hello
from diagnostics import filter_habits as fh

HOST = "blocked.example"
IP = "45.1.2.3"


class _Filter:
    """Учебный фильтр: режет приветствие, если видит запрещённое имя тем способом, каким умеет читать.

    ``joins_packets`` — склеивает куски пакета; ``join_wait`` — сколько секунд ждёт
    остаток (None — сколько угодно); ``joins_records`` — разбирает несколько
    записей TLS; ``server_takes`` — терпит ли сервер такое дробление вообще.
    """

    def __init__(self, *, joins_packets=False, join_wait=None, joins_records=False, server_takes=lambda parts: True, blocked=(HOST,), cut="reset") -> None:
        self.joins_packets = joins_packets
        self.join_wait = join_wait
        self.joins_records = joins_records
        self.server_takes = server_takes
        self.blocked = tuple(name.encode() for name in blocked)
        self.cut = cut
        self.sent: list[tuple[tuple[bytes, ...], float]] = []

    def _sees(self, parts: tuple[bytes, ...], pause: float) -> bool:
        joined = self.joins_packets and (self.join_wait is None or pause < self.join_wait)
        packets = [b"".join(parts)] if joined else list(parts)
        views: list[bytes] = []
        for packet in packets:
            records, offset = [], 0
            while offset + 5 <= len(packet) and packet[offset] == 22:
                size = int.from_bytes(packet[offset + 3 : offset + 5], "big")
                records.append(packet[offset + 5 : offset + 5 + size])
                offset += 5 + size
            if not records:
                # Кусок без заголовка записи: фильтр видит его как есть.
                views.append(packet)
            elif self.joins_records:
                views.append(b"".join(records))
            else:
                views.extend(records)
        return any(name in view for view in views for name in self.blocked)

    def __call__(self, ip: str, parts: tuple[bytes, ...], pause: float) -> str:
        self.sent.append((parts, pause))
        if self._sees(parts, pause):
            return self.cut
        return "ok" if self.server_takes(parts) else "timeout"


def _facts(network) -> fh.SplitFacts:
    return fh.check_split(HOST, IP, send=network)


def _verdict(network) -> fh.SplitVerdict:
    return fh.judge_split(_facts(network))


def _states(verdict) -> dict[str, str]:
    return {way.way: way.state for way in verdict.ways}


class PartsTests(unittest.TestCase):
    @staticmethod
    def _joined(parts: tuple[bytes, ...]) -> bytes:
        """Приветствие одной записью — каким его соберёт сервер."""
        data, bodies, offset = b"".join(parts), [], 0
        while offset + 5 <= len(data):
            size = int.from_bytes(data[offset + 3 : offset + 5], "big")
            bodies.append(data[offset + 5 : offset + 5 + size])
            offset += 5 + size
        body = b"".join(bodies)
        return data[:3] + len(body).to_bytes(2, "big") + body

    def test_every_way_carries_the_whole_hello_and_cuts_where_it_says(self) -> None:
        name = HOST.encode()
        for way in fh.WAYS:
            parts, pause = fh._parts(HOST, way)
            with self.subTest(way=way):
                # Сервер, склеив куски и записи, получает то же приветствие с тем же именем.
                self.assertEqual(browser_hello.parse_hello(self._joined(parts))[2], HOST)
                self.assertEqual(pause, fh.SLOW_PAUSE_S if way == fh.WAY_TCP_SLOW else 0.0)
        first, rest = fh._parts(HOST, fh.WAY_TCP_FIRST)[0]
        self.assertEqual((len(first), name in rest), (1, True))
        before, after = fh._parts(HOST, fh.WAY_TCP_BEFORE)[0]
        self.assertTrue(after.startswith(name))
        self.assertNotIn(name, before)
        # Разрез посреди имени: целиком имени нет ни в одном куске.
        for way in (fh.WAY_TCP_NAME, fh.WAY_TCP_SLOW, fh.WAY_TCP_THREE, fh.WAY_REC_TCP):
            self.assertTrue(all(name not in part for part in fh._parts(HOST, way)[0]), way)
        self.assertEqual(len(fh._parts(HOST, fh.WAY_TCP_THREE)[0]), 3)

    def test_record_ways_really_send_two_records(self) -> None:
        for way in (fh.WAY_REC_BEFORE, fh.WAY_REC_NAME):
            packet = fh._parts(HOST, way)[0][0]
            size = int.from_bytes(packet[3:5], "big")
            self.assertEqual(packet[5 + size], 22, way)
            self.assertLess(5 + size, len(packet))


class CollectTests(unittest.TestCase):
    def test_ways_go_one_after_another_with_a_rest_between_connections(self) -> None:
        rests: list[float] = []
        network = _Filter(joins_packets=True, joins_records=True)
        facts = fh.check_split(HOST, IP, send=network, pause=rests.append)

        self.assertEqual([item.way for item in facts.ways], list(fh.WAYS))
        self.assertEqual(len(rests), len(network.sent))
        self.assertEqual(set(rests), {fh.BETWEEN_S})
        # Контроль с безобидным именем — перед пробами с именем сайта, и ещё раз в самом конце.
        self.assertNotIn(HOST.encode(), b"".join(network.sent[0][0]))
        self.assertEqual(facts.final_control, "ok")

    def test_whole_hello_that_passes_ends_the_check_at_once(self) -> None:
        network = _Filter(blocked=())
        facts = _facts(network)

        self.assertEqual([item.way for item in facts.ways], [fh.WAY_WHOLE])
        self.assertEqual(len(network.sent), 1 + fh.REPEATS)

    def test_single_failures_are_rechecked(self) -> None:
        # Первый контроль пропал — берётся второй; две попытки разошлись — делается третья.
        answers = iter(["timeout", "ok", "reset", "ok", "reset"])
        facts = fh.check_split(HOST, IP, send=lambda *_a: next(answers, "ok"))
        whole = facts.ways[0]

        self.assertEqual(whole.control, ("timeout", "ok"))
        self.assertEqual(whole.real, ("reset", "ok", "reset"))
        self.assertEqual(fh._way_state(whole), fh.CUT)

    def test_way_without_a_working_control_is_not_tried_with_the_real_name(self) -> None:
        network = _Filter(joins_packets=True, joins_records=True, server_takes=lambda parts: len(parts) == 1)
        facts = _facts(network)
        by_way = {item.way: item for item in facts.ways}

        self.assertEqual(by_way[fh.WAY_TCP_NAME].control, ("timeout", "timeout"))
        self.assertEqual(by_way[fh.WAY_TCP_NAME].real, ())

    def test_cancelled_probe_gives_no_verdict(self) -> None:
        self.assertIsNone(_verdict(lambda *_a: "cancelled"))


class SplitJudgeTests(unittest.TestCase):
    def test_filter_reading_single_packets_is_beaten_by_any_split(self) -> None:
        verdict = _verdict(_Filter())

        self.assertEqual(verdict.code, fh.HABIT_TCP)
        self.assertIn("pos=midsld", verdict.text)
        self.assertIn("multisplit", verdict.advice)
        self.assertEqual(_states(verdict)[fh.WAY_WHOLE], fh.CUT)
        self.assertEqual(_states(verdict)[fh.WAY_TCP_NAME], fh.PASSED)

    def test_filter_joining_only_what_comes_at_once_is_told_apart(self) -> None:
        verdict = _verdict(_Filter(joins_packets=True, join_wait=0.5, joins_records=True))

        self.assertEqual(verdict.code, fh.HABIT_TIMER)
        self.assertEqual(_states(verdict)[fh.WAY_TCP_NAME], fh.CUT)
        self.assertEqual(_states(verdict)[fh.WAY_TCP_SLOW], fh.PASSED)
        # Zapret шлёт куски подряд: простое дробление такому фильтру не помеха.
        self.assertIn("подряд", verdict.advice)

    def test_filter_joining_packets_but_not_records_needs_record_split(self) -> None:
        verdict = _verdict(_Filter(joins_packets=True))

        self.assertEqual(verdict.code, fh.HABIT_RECORDS)
        self.assertIn("tlsrec", verdict.advice)

    def test_filter_joining_everything_makes_splitting_useless(self) -> None:
        verdict = _verdict(_Filter(joins_packets=True, joins_records=True))

        self.assertEqual(verdict.code, fh.HABIT_NONE)
        self.assertIn("fake", verdict.advice)
        self.assertTrue(all(state == fh.CUT for state in _states(verdict).values()))

    def test_ways_the_server_does_not_take_are_not_blamed_on_the_filter(self) -> None:
        # Сервер не терпит разрезанный пакет: по этим способам вывода нет, и «собирает всё» сказать нельзя.
        verdict = _verdict(_Filter(joins_packets=True, joins_records=True, server_takes=lambda parts: len(parts) == 1))
        states = _states(verdict)

        self.assertEqual(states[fh.WAY_TCP_NAME], fh.NO_CONTROL)
        self.assertEqual(states[fh.WAY_REC_NAME], fh.CUT)
        self.assertEqual(verdict.code, fh.HABIT_UNKNOWN)
        way = next(item for item in verdict.ways if item.way == fh.WAY_TCP_NAME)
        self.assertEqual((way.real, way.control), ("", "молчание ×2"))
        self.assertIn("не различить", way.meaning)

    def test_no_working_control_for_the_whole_hello_is_said_plainly(self) -> None:
        verdict = _verdict(_Filter(server_takes=lambda parts: False))

        self.assertEqual(verdict.code, fh.HABIT_UNKNOWN)
        self.assertIn("даже безобидное имя", verdict.text)
        self.assertNotIn("не каждый раз", verdict.text)

    def test_whole_hello_cut_now_and_then_gives_no_comparison(self) -> None:
        answers = iter(["ok", "reset", "ok", "ok"])
        verdict = fh.judge_split(fh.check_split(HOST, IP, send=lambda *_a: next(answers, "ok")))

        self.assertEqual(verdict.code, fh.HABIT_NOT_BY_NAME)
        answers = iter(["ok", "reset", "ok", "connect"])
        verdict = fh.judge_split(fh.check_split(HOST, IP, send=lambda *_a: next(answers, "ok")))
        self.assertEqual(verdict.code, fh.HABIT_UNKNOWN)
        self.assertIn("не каждый раз", verdict.text)

    def test_address_closed_by_the_end_voids_the_result(self) -> None:
        facts = _facts(_Filter())
        punished = fh.SplitFacts(facts.host, facts.ip, facts.ways, "timeout")

        verdict = fh.judge_split(punished)
        self.assertEqual(verdict.code, fh.HABIT_UNKNOWN)
        self.assertIn("закрыл адрес", verdict.text)

    def test_foreign_answer_counts_as_cut_and_outcomes_are_named(self) -> None:
        verdict = _verdict(_Filter(cut="garbage"))

        self.assertEqual(_states(verdict)[fh.WAY_WHOLE], fh.CUT)
        self.assertEqual(verdict.ways[0].real, "чужой ответ ×2")
        self.assertEqual(fh.outcomes_text(("ok", "reset", "reset")), "прошло, сброс ×2")
        self.assertEqual(fh.outcomes_text(("alert", "connect")), "дошло до сервера, нет соединения")


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


class SiteChoiceTests(unittest.TestCase):
    def test_sites_come_from_different_services_and_networks(self) -> None:
        # Три адреса одного сервиса показали бы одно и то же трижды: берём по одному с сервиса.
        from concurrent.futures import ThreadPoolExecutor
        from types import SimpleNamespace
        from unittest.mock import patch

        from diagnostics import block_cause, sections

        def probe(host, ip, code=block_cause.CAUSE_BY_NAME):
            return SimpleNamespace(host=host, reach=SimpleNamespace(ip=ip, ok=False), cause=SimpleNamespace(code=code))

        collected = {
            "discord": [probe("discord.com", "162.159.1.1"), probe("gateway.discord.gg", "162.159.2.2")],
            "x": [probe("x.com", "151.101.1.1")],
            "linkedin": [probe("www.linkedin.com", "162.159.9.9")],
            "rutracker": [probe("rutracker.org", "172.67.1.1")],
            "telegram": [probe("telegram.org", "149.154.1.1", block_cause.CAUSE_ADDRESS_SILENT)],
        }
        asked: list[str] = []

        def fake_split(host, ip, **_kwargs):
            asked.append(host)
            return fh.SplitFacts(host, ip, cancelled=True)

        with ThreadPoolExecutor(4) as pool:
            run = SimpleNamespace(
                submit=pool.submit, wait=lambda future: future.result(), dns_cancelled=lambda: False, probe_cancel=None
            )
            with patch.object(fh, "check_split", fake_split):
                sections.check_filter_habits(run, collected, lambda _line: None)

        self.assertEqual(sorted(asked), ["discord.com", "rutracker.org", "x.com"])


class ReportTests(unittest.TestCase):
    def _report(self, *filters, ech=(fh.ECH_FINE, "соединения с ECH проходят"), tools=()):
        return fh.summarize([_verdict(item) for item in filters], ech, tools=tools)

    def test_same_habit_on_every_site_becomes_the_headline_with_advice(self) -> None:
        report = self._report(_Filter(joins_packets=True), _Filter(joins_packets=True))

        self.assertIn("записи шифрования — нет", report["headline"])
        self.assertIn("tlsrec", report["advice"])
        text = "\n".join(fh.lines(report))
        self.assertIn("❌ Разрез пакета посреди имени — режется: с именем сайта: сброс ×2 · с безобидным именем: прошло", text)
        self.assertIn("✅ Две записи TLS, граница посреди имени — проходит", text)
        self.assertIn("не испытываются", text)

    def test_different_habits_are_not_merged_into_one_advice(self) -> None:
        report = self._report(_Filter(), _Filter(joins_packets=True, joins_records=True))

        self.assertIn("по-разному", report["headline"])
        self.assertEqual(report["advice"], "")

    def test_running_bypass_tools_turn_the_result_into_a_warning_without_strategy_advice(self) -> None:
        # Пробы идут через Zapret и VPN: это сеть вместе с обходом, советовать стратегии по ней нельзя.
        report = self._report(_Filter(), tools=("Zapret", "sing-box"))

        self.assertIn("вместе с Zapret, sing-box", report["headline"])
        self.assertIn("Остановите", report["advice"])
        self.assertEqual(report["sites"][0]["advice"], "")
        self.assertEqual(report["disturbed"], ["Zapret", "sing-box"])
        [card] = build_cards({"habits": report})
        self.assertEqual((card.level, card.status), ("warn", "Мешает обход"))

    def test_nothing_to_show_gives_no_section(self) -> None:
        self.assertIsNone(fh.summarize([], (fh.ECH_UNKNOWN, "")))

    def test_card_shows_every_way_with_both_outcomes_and_warns_about_blocked_ech(self) -> None:
        report = self._report(_Filter(), ech=(fh.ECH_BLOCKED, "соединения с ECH режутся"))
        [card] = build_cards({"habits": report})

        self.assertEqual((card.key, card.level, card.status), ("habits", "warn", "Не склеивает пакеты"))
        titles = [section.title for section in card.sections]
        self.assertEqual(titles, ["Что делает фильтр", f"{HOST} ({IP})", "Чего эта проверка не видит", "Cloudflare и ECH"])
        rows = {line.name: (line.state, line.text) for line in card.sections[1].lines}
        self.assertEqual(rows["Целиком, одним пакетом"][0], "fail")
        self.assertIn("с именем сайта: сброс ×2 · с безобидным именем: прошло — фильтр его режет", rows["Целиком, одним пакетом"][1])
        self.assertEqual(rows["Разрез пакета посреди имени"][0], "ok")
        self.assertIn("chrome://flags", card.sections[-1].lines[-1].text)

    def test_card_status_tells_when_there_is_no_verdict(self) -> None:
        report = self._report(_Filter(server_takes=lambda parts: False))
        [card] = build_cards({"habits": report})

        self.assertEqual(card.status, "Вывода нет")
        self.assertIn("даже безобидное имя", report["headline"])


if __name__ == "__main__":
    unittest.main()
