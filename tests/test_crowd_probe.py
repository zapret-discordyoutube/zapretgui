"""Заморозка от нескольких соединений сразу: одно проходит, после пачки сайт молчит."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import crowd_probe as cp
from diagnostics import sections
from diagnostics.block_cause import HELLO_CANCELLED, HELLO_OK, HELLO_RESET, HELLO_TIMEOUT
from diagnostics.verdict import ReachState

HOST, IP = "site.example", "45.1.2.3"


class _Line:
    """Учебная линия: сайт отвечает, пока к нему не пришла пачка; потом молчит ``frozen_for`` секунд."""

    def __init__(self, *, freezes=False, frozen_for=120.0, crowd=None, before=HELLO_OK, control=HELLO_OK, after=None):
        self.freezes, self.frozen_for = freezes, frozen_for
        self.crowd_answers, self.before, self.control_answer, self.after = crowd, before, control, after
        self.now = 0.0
        self.frozen_until = -1.0
        self.asked: list[str] = []
        self.waits: list[float] = []

    def hello(self, ip, host):
        self.asked.append("one")
        singles = self.asked.count("one")
        if singles == 1:
            return self.before
        if self.after is not None and singles == 2:
            return self.after
        return HELLO_TIMEOUT if self.now < self.frozen_until else HELLO_OK

    def together(self, calls):
        self.asked.append(f"crowd:{len(calls)}")
        if self.freezes:
            self.frozen_until = self.now + self.frozen_for
            return [HELLO_OK] * 3 + [HELLO_TIMEOUT] * (len(calls) - 3)
        return list(self.crowd_answers or [HELLO_OK] * len(calls))

    def control(self):
        self.asked.append("control")
        return self.control_answer

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return True

    def collect(self, **kwargs) -> cp.CrowdFacts:
        return cp.collect(
            HOST, IP, hello=self.hello, together=self.together, control=self.control, wait=self.wait, clock=lambda: self.now, **kwargs
        )


class CrowdTests(unittest.TestCase):
    def test_site_that_takes_the_crowd_is_not_frozen(self) -> None:
        line = _Line()
        verdict = cp.judge(line.collect())

        self.assertEqual(verdict.code, cp.CROWD_NONE)
        self.assertEqual(line.asked, ["one", f"crowd:{cp.CROWD}", "one"])
        # Сайт не замолчал — ждать и спрашивать контроль незачем.
        self.assertEqual(line.waits, [])

    def test_site_silent_after_the_crowd_that_comes_back_is_a_freeze(self) -> None:
        line = _Line(freezes=True, frozen_for=120.0)
        facts = line.collect()
        verdict = cp.judge(facts)

        self.assertEqual(verdict.code, cp.CROWD_FREEZE)
        self.assertEqual(facts.recovered_s, 120.0)
        self.assertIn("снова ответил через 120 с", verdict.text)
        # Честно сказано, что остановку вызвала сама проверка.
        self.assertIn("вызвала сама проверка", verdict.advice)
        self.assertIn("control", line.asked)

    def test_site_that_does_not_come_back_is_said_so_without_calling_it_a_freeze(self) -> None:
        line = _Line(freezes=True, frozen_for=10_000.0)
        facts = line.collect()
        verdict = cp.judge(facts)

        self.assertEqual(verdict.code, cp.CROWD_STUCK)
        self.assertIsNone(facts.recovered_s)
        self.assertLessEqual(facts.waited_s, cp.RECOVERY_WAIT_S + cp.RECOVERY_STEP_S)
        self.assertIn("Подождите несколько минут", verdict.advice)

    def test_waiting_respects_the_time_left_for_the_whole_check(self) -> None:
        line = _Line(freezes=True, frozen_for=10_000.0)
        facts = line.collect(max_wait=25.0)

        self.assertLessEqual(facts.waited_s, 25.0 + cp.RECOVERY_STEP_S)
        self.assertEqual(cp.judge(facts).code, cp.CROWD_STUCK)

    def test_dead_line_is_not_blamed_on_the_filter(self) -> None:
        # Замолчал и контрольный сайт — пропала связь, а не «заморозили сайт».
        line = _Line(freezes=True, control=HELLO_TIMEOUT)
        verdict = cp.judge(line.collect())

        self.assertEqual(verdict.code, cp.CROWD_UNKNOWN)
        self.assertIn("пропала сама связь", verdict.text)
        self.assertEqual(line.waits, [])

    def test_refusals_inside_the_crowd_with_a_live_site_are_the_sites_own_limit(self) -> None:
        line = _Line(crowd=[HELLO_OK, HELLO_OK, HELLO_RESET, HELLO_RESET])
        verdict = cp.judge(line.collect())

        self.assertEqual(verdict.code, cp.CROWD_LIMIT)
        self.assertIn("ограничение самого сайта", verdict.text)

    def test_reset_after_the_crowd_is_not_a_freeze(self) -> None:
        line = _Line(after=HELLO_RESET)
        verdict = cp.judge(line.collect())

        self.assertEqual(verdict.code, cp.CROWD_UNKNOWN)
        self.assertIn("заморозка выглядит иначе", verdict.text)

    def test_site_that_fails_alone_is_not_crowded_at_all(self) -> None:
        line = _Line(before=HELLO_TIMEOUT)
        verdict = cp.judge(line.collect())

        self.assertEqual(verdict.code, cp.CROWD_UNKNOWN)
        self.assertEqual(line.asked, ["one"])

    def test_rows_tell_each_connection_in_words(self) -> None:
        # Экран коды исходов в слова не переводит: строки приходят готовыми.
        frozen = cp.rows(_Line(freezes=True, frozen_for=120.0).collect())
        texts = [(row["title"], row["text"], row["state"]) for row in frozen]

        self.assertEqual(texts[0], ("Одно соединение до пачки", "ответило", "ok"))
        self.assertIn(("Соединение 4 из 4 одновременных", "без ответа", "fail"), texts)
        self.assertIn(("Одно соединение сразу после пачки", "без ответа", "fail"), texts)
        self.assertIn(("Контрольный сайт в это же время", "отвечает", "ok"), texts)
        self.assertEqual(texts[-1], ("Сайт снова ответил", "через 120 с", "info"))

        calm = cp.rows(_Line().collect())
        self.assertEqual(len(calm), 1 + cp.CROWD + 1)
        self.assertEqual({row["state"] for row in calm}, {"ok"})

        stuck = cp.rows(_Line(freezes=True, frozen_for=10_000.0).collect())
        self.assertEqual((stuck[-1]["title"], stuck[-1]["state"]), ("Ждали возвращения сайта", "warn"))

    def test_every_outcome_has_a_status_word(self) -> None:
        codes = (cp.CROWD_NONE, cp.CROWD_FREEZE, cp.CROWD_STUCK, cp.CROWD_LIMIT, cp.CROWD_UNKNOWN)

        self.assertEqual(set(cp.STATUS), set(codes))

    def test_cancel_gives_no_verdict(self) -> None:
        self.assertIsNone(cp.judge(None))
        self.assertIsNone(cp.judge(cp.CrowdFacts(HOST, IP, HELLO_OK, (HELLO_CANCELLED,) * 4, HELLO_OK)))


class SectionTests(unittest.TestCase):
    """Какой сайт идёт на пробу и когда она не делается вовсе."""

    @staticmethod
    def _probe(host, *, state=ReachState.OK, main=True, browser="ok", ip="45.1.2.3"):
        lines = [SimpleNamespace(key="browser", state=browser)] if browser else []
        return SimpleNamespace(
            host=host, reach_state=state, reach=SimpleNamespace(ip=ip), target=SimpleNamespace(main=main), protocols=lines, unstable=""
        )

    def _run(self, collected, *, tools=(), verdict=None):
        services = {
            "ya": SimpleNamespace(label="Яндекс", control=True),
            "a": SimpleNamespace(label="A", control=False),
            "b": SimpleNamespace(label="B", control=False),
        }
        asked: list[str] = []

        def collect(host, ip, **_kwargs):
            asked.append(host)
            return cp.CrowdFacts(host, ip, HELLO_OK, (HELLO_OK,) * 4, HELLO_OK)

        run = SimpleNamespace(dns_cancelled=lambda: False, probe_cancel=None, submit=None, deadline=10**9)
        lines: list[str] = []
        with patch.object(cp, "collect", side_effect=collect):
            result = sections.check_crowd(run, collected, services, lines.append, tools=tools)
        return result, asked, lines

    def test_first_open_site_whose_browser_hello_passes_is_taken(self) -> None:
        collected = {
            "ya": [self._probe("ya.ru")],
            "a": [self._probe("blocked.example", state=ReachState.DPI), self._probe("side.example", main=False)],
            "b": [self._probe("b.example")],
        }
        result, asked, lines = self._run(collected)

        self.assertEqual(asked, ["b.example"])
        self.assertEqual((result["state"], result["level"], result["label"]), (cp.CROWD_NONE, "ok", "B"))
        self.assertEqual(result["crowd"], [HELLO_OK] * 4)
        self.assertEqual(result["status"], "Не замирает")
        self.assertEqual(len(result["rows"]), 6)
        self.assertTrue(any("Несколько соединений сразу" in line for line in lines))

    def test_probe_is_not_run_with_a_bypass_working(self) -> None:
        collected = {"ya": [self._probe("ya.ru")], "b": [self._probe("b.example")]}
        result, asked, _lines = self._run(collected, tools=("Zapret",))

        self.assertIsNone(result)
        self.assertEqual(asked, [])

    def test_no_suitable_site_or_no_control_no_probe(self) -> None:
        only_blocked = {"ya": [self._probe("ya.ru")], "a": [self._probe("x.example", state=ReachState.DPI)]}
        no_browser = {"ya": [self._probe("ya.ru")], "a": [self._probe("x.example", browser="")]}
        no_control = {"ya": [self._probe("ya.ru", state=ReachState.DPI)], "a": [self._probe("x.example")]}

        for collected in (only_blocked, no_browser, no_control):
            self.assertEqual(self._run(collected)[:2], (None, []))

    def test_freeze_becomes_a_problem_with_the_site_named(self) -> None:
        crowd = {"level": "warn", "text": "Сайт замолчал", "advice": "Подберите стратегию", "host": "b.example", "label": "B"}
        found = sections.crowd_problems(crowd, zapret_running=False)

        self.assertEqual((found[0]["level"], found[0]["target"], found[0]["title"], found[0]["action"]), ("warn", "b.example", "B", "start_zapret"))
        self.assertEqual(sections.crowd_problems({"level": "ok"}, zapret_running=False), [])
        self.assertEqual(sections.crowd_problems(None, zapret_running=False), [])


class EngineTests(unittest.TestCase):
    """Проба входит в полную проверку, идёт последней и знает, что работает обход."""

    FROZEN = {
        "state": "freeze",
        "level": "warn",
        "host": "discord.com",
        "address": "162.159.137.232",
        "label": "Discord",
        "text": "Discord.com: после 4 соединений сразу сайт замолчал и снова ответил через 118 с",
        "advice": "Подберите стратегию.",
    }

    def _run(self, scope: str):
        from test_diagnostics_verdict import _Net

        from diagnostics import engine

        net = _Net()
        net.crowd = dict(self.FROZEN)
        order: list[str] = []
        speed, crowd = net.speed, net._crowd
        net._crowd = lambda *args, **kwargs: order.append("crowd") or crowd(*args, **kwargs)
        with patch.object(sections, "check_speed", side_effect=lambda *_args: order.append("speed") or speed):
            report = net.run(engine.run_blockcheck, scope, emit=lambda _line: None)
        return net, report, order

    def test_full_check_runs_the_probe_last_and_reports_the_freeze(self) -> None:
        net, report, order = self._run("full")

        self.assertEqual(report["crowd"]["state"], "freeze")
        problem = next(item for item in report["problems"] if "замолчал" in item["text"])
        self.assertEqual((problem["level"], problem["target"], problem["title"]), ("warn", "discord.com", "Discord"))
        # Zapret в сценариях движка «запущен»: проба об этом знает и сама решает не идти.
        self.assertEqual(net.crowd_tools, ["Zapret"])

    def test_short_checks_do_not_run_it(self) -> None:
        net, report, _order = self._run("main")

        self.assertIsNone(report["crowd"])
        self.assertIsNone(net.crowd_tools)


if __name__ == "__main__":
    unittest.main()
