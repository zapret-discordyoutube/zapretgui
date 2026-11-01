"""Подбор стратегий и качество передачи: пауза сервера не стратегия, но о ней надо сказать."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, replace

from blockcheck.strategy_search import verdict as rules
from blockcheck.strategy_search.verdict import ProbeOutcome
from diagnostics import transfer_probe as tp
from test_strategy_search_engine import OK, FakeEnv, FakeEvents, blocked_unless, cand, run

PAUSE_TEXT = "1 ждал из 7 запросов ответа до 2.9 с, остальные — около 9 мс: сервер отвечает с паузами"


@dataclass
class PausingEnv(FakeEnv):
    """Проба объёма всегда добавляет вывод о паузах — как сервер, который отвечает рывками."""

    transfer: str = rules.SLOW_TRANSFER_CODES[0]

    def probe_volume(self, host, address, answered):
        outcome = super().probe_volume(host, address, answered)
        return replace(outcome, transfer=self.transfer, transfer_text=PAUSE_TEXT)


def _verdict_cut():
    from diagnostics import volume_probe as vp

    return vp.VolumeVerdict(vp.VOLUME_CUT, "соединение замирает", cut_kb=14)


def _verdict_ok():
    from diagnostics import volume_probe as vp

    return vp.VolumeVerdict(vp.VOLUME_OK, "прошло")


class RulesTests(unittest.TestCase):
    def _transfer(self, code=tp.TRANSFER_PAUSE) -> tp.TransferVerdict:
        return tp.TransferVerdict(code, "warn", PAUSE_TEXT, samples=7, confidence=tp.CONFIDENCE_MEDIUM)

    def test_volume_outcome_carries_the_transfer_verdict(self) -> None:
        for verdict in (_verdict_ok(), _verdict_cut()):
            with self.subTest(code=verdict.code):
                outcome = rules.judge_volume(OK, verdict, self._transfer())
                self.assertEqual((outcome.transfer, outcome.transfer_text), ("pause", PAUSE_TEXT))

    def test_without_a_transfer_verdict_the_fields_are_empty(self) -> None:
        outcome = rules.judge_volume(OK, _verdict_ok())

        self.assertEqual((outcome.transfer, outcome.transfer_text, outcome.volume), ("", "", rules.VOLUME_OK))

    def test_transfer_problem_names_only_slow_codes(self) -> None:
        fast = replace(OK, transfer="ok", transfer_text="быстро")
        slow = replace(OK, transfer="slow_reply", transfer_text="медленно")

        self.assertEqual(rules.transfer_problem([OK, fast]), "")
        self.assertEqual(rules.transfer_problem([fast, slow]), "медленно")

    def test_worst_transfer_prefers_pause_over_slow_body(self) -> None:
        outcomes = [replace(OK, transfer="slow_body"), replace(OK, transfer="pause"), replace(OK, transfer="ok")]

        self.assertEqual(rules.worst_transfer(outcomes), "pause")
        self.assertEqual(rules.worst_transfer([OK, replace(OK, transfer="ok")]), "")


class BaselineTests(unittest.TestCase):
    def test_open_but_slow_target_says_it_is_not_the_strategy(self) -> None:
        env = PausingEnv(network=lambda running, n: OK, candidates=[cand("a")])
        _report, events = run(env, FakeEvents(answer=False))

        self.assertEqual(len(events.questions), 1)
        question = events.questions[0]
        self.assertIn("подбирать нечего", question)
        self.assertIn("медленно", question)
        self.assertIn("стратегия обхода тут не поможет", question)
        self.assertTrue(any("с паузами" in line for line in events.logs))

    def test_open_and_fast_target_gets_the_old_question(self) -> None:
        env = FakeEnv(network=lambda running, n: OK, candidates=[cand("a")])
        _report, events = run(env, FakeEvents(answer=False))

        self.assertNotIn("медленно", events.questions[0])
        self.assertFalse(any("с паузами" in line for line in events.logs))


class StrategyResultTests(unittest.TestCase):
    def _env(self, **extra):
        # Без обхода короткая страница замирает на объёме; «good» это чинит, а сервер всё равно отвечает с паузами.
        return PausingEnv(
            network=lambda running, n: OK,
            candidates=[cand("good")],
            volume=lambda running, address: rules.VOLUME_OK if running == "good" else rules.VOLUME_CUT,
            **extra,
        )

    def test_working_strategy_is_marked_with_the_servers_pause(self) -> None:
        _report, events = run(self._env())

        [result] = events.results
        self.assertEqual(result.verdict, rules.VERDICT_WORKING)
        self.assertEqual(result.raw_data["transfer"], "pause")
        self.assertTrue(any("от стратегии это не зависит" in line for line in events.logs))

    def test_fast_server_leaves_the_mark_empty(self) -> None:
        env = FakeEnv(
            network=lambda running, n: OK,
            candidates=[cand("good")],
            volume=lambda running, address: rules.VOLUME_OK if running == "good" else rules.VOLUME_CUT,
        )
        _report, events = run(env)

        self.assertEqual(events.results[0].raw_data["transfer"], "")
        self.assertFalse(any("от стратегии это не зависит" in line for line in events.logs))

    def test_a_pause_does_not_fail_a_strategy(self) -> None:
        """Пауза — свойство сервера: стратегия, которая открыла цель, остаётся рабочей."""
        _report, events = run(self._env())

        self.assertEqual(events.results[0].verdict, rules.VERDICT_WORKING)
        self.assertEqual(events.results[0].success, True)


class SearchNotBlockedByPauseTests(unittest.TestCase):
    def test_blocked_target_still_finds_the_strategy_even_if_replies_pause(self) -> None:
        env = PausingEnv(network=blocked_unless("good"), candidates=[cand("bad"), cand("good")])
        _report, events = run(env)

        by_id = {result.strategy_id: result.verdict for result in events.results}
        self.assertEqual(by_id["good"], rules.VERDICT_WORKING)
        self.assertEqual(by_id["bad"], rules.VERDICT_FAILED)


if __name__ == "__main__":
    unittest.main()


class PresentationTests(unittest.TestCase):
    def _result(self, raw_data: dict):
        from blockcheck.scan_models import StrategyProbeResult

        return StrategyProbeResult(
            strategy_name="good",
            strategy_id="good",
            strategy_args="--lua-desync=fake",
            target="x.com",
            success=True,
            time_ms=120.0,
            verdict="working",
            attempts_ok=3,
            attempts_total=3,
            apply_lines=("--lua-desync=fake",),
            raw_data=raw_data,
        )

    def test_working_row_tells_the_pause_is_not_the_strategy(self) -> None:
        from blockcheck.strategy_scan_page_plans import build_result_presentation

        paused = build_result_presentation(self._result({"transfer": "pause"}), row_number=1)
        plain = build_result_presentation(self._result({"transfer": ""}), row_number=1)

        self.assertIn("от стратегии это не зависит", paused.status_tooltip)
        self.assertNotIn("от стратегии это не зависит", plain.status_tooltip)
        # Строка остаётся «Работает»: пауза сервера стратегию не опровергает.
        self.assertTrue(paused.status_text.startswith("Работает"))
        self.assertTrue(paused.can_apply)

    def test_window_row_says_the_slowness_is_the_window_not_the_server(self) -> None:
        from blockcheck.strategy_scan_page_plans import build_result_presentation

        row = build_result_presentation(self._result({"transfer": "slow", "window_addresses": ["74.125.110.106"]}), row_number=1)

        self.assertIn("малым окном приёма (wssize) для 74.125.110.106", row.status_tooltip)
        self.assertNotIn("от стратегии это не зависит", row.status_tooltip)
