"""Оценка передачи в проверке сайтов: движок, текст отчёта, дорога, метка и строка карточки."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from blockcheck.ui.result_cards_model import build_cards
from blockcheck_words import report_with_words
from diagnostics import engine, report_text, service_view
from diagnostics import transfer_probe as tp
from diagnostics import volume_probe as vp
from diagnostics.tls_probe import KIND_OK, ProbeResult
from diagnostics.verdict import ReachState

S = tp.RequestSample
FIXTURE = Path(__file__).parent / "fixtures" / "blockcheck_report_sample.json"


def _samples(*millis: float) -> tuple[tp.RequestSample, ...]:
    return (S(ttfb_ms=300, fresh=True), *(S(received=5_000, ttfb_ms=ms) for ms in millis))


def _facts(samples) -> vp.VolumeFacts:
    return vp.VolumeFacts(first=vp.VolumeRun(vp.RUN_PASSED, 49_500, len(samples), tuple(samples)))


class EngineTransferTests(unittest.TestCase):
    def _check(self, samples) -> engine._Probe:
        probe = engine._Probe(target=engine.Target("x.com", "сайт", main=True), service="x", host="x.com")
        probe.reach = ProbeResult(ip="203.0.113.5", kind=KIND_OK, status=200, body_size=500)
        probe.reach_state = ReachState.OK
        run = engine._Run(None, workers=2, deadline=60)
        self.addCleanup(run.close)
        with patch.object(vp, "collect", return_value=_facts(samples)):
            engine._check_volume(run, probe)
        return probe

    def test_confirmed_pause_makes_the_site_open_through_once_in_a_while(self) -> None:
        probe = self._check(_samples(9, 42, 7, 12, 9, 9, 2_861, 2_900))

        self.assertEqual(probe.transfer.code, tp.TRANSFER_PAUSE)
        self.assertIn("рывками", probe.unstable)
        self.assertEqual(probe.reach_state, ReachState.OK)

    def test_single_unconfirmed_pause_is_only_a_report_line(self) -> None:
        probe = self._check(_samples(9, 9, 9, 3_000))

        self.assertEqual((probe.transfer.code, probe.transfer.confidence), (tp.TRANSFER_PAUSE, tp.CONFIDENCE_LOW))
        self.assertEqual(probe.unstable, "")

    def test_fast_answers_leave_no_complaint(self) -> None:
        probe = self._check(_samples(20, 25, 30, 22, 24, 21))

        self.assertEqual((probe.transfer.code, probe.unstable), (tp.TRANSFER_OK, ""))

    def test_pause_does_not_replace_a_stronger_instability_reason(self) -> None:
        probe = engine._Probe(target=engine.Target("x.com", "сайт", main=True), service="x", host="x.com")
        probe.reach = ProbeResult(ip="203.0.113.5", kind=KIND_OK, status=200, body_size=500)
        probe.reach_state = ReachState.OK
        probe.unstable = "из трёх попыток подряд прошла одна"
        run = engine._Run(None, workers=2, deadline=60)
        self.addCleanup(run.close)
        with patch.object(vp, "collect", return_value=_facts(_samples(9, 9, 9, 9, 9, 3_000, 3_100))):
            engine._check_volume(run, probe)

        self.assertEqual(probe.unstable, "из трёх попыток подряд прошла одна")


class ReportTransferTests(unittest.TestCase):
    def _probe(self, verdict) -> engine._Probe:
        probe = engine._Probe(target=engine.Target("x.com", "сайт", main=True), service="x", host="x.com")
        probe.reach = ProbeResult(ip="203.0.113.5", kind=KIND_OK, status=200, body_size=500)
        probe.reach_state = ReachState.OK
        probe.transfer = verdict
        return probe

    def test_target_report_carries_the_numbers(self) -> None:
        verdict = tp.judge(_samples(9, 42, 7, 12, 9, 9, 2_861))
        report = report_text.target_report(self._probe(verdict))

        self.assertEqual(report["transfer"]["code"], "pause")
        self.assertEqual(report["transfer"]["ttfb_max_ms"], 2_861)
        self.assertIsNone(report_text.target_report(self._probe(None))["transfer"])

    def test_text_report_has_a_line_unless_unknown(self) -> None:
        pause = tp.judge(_samples(9, 42, 7, 12, 9, 9, 2_861))
        lines = report_text.probe_lines(self._probe(pause), full=True)

        self.assertTrue(any("Скорость ответа" in line and "⚠️" in line for line in lines))
        unknown = tp.judge(())
        self.assertFalse(any("Скорость ответа" in line for line in report_text.probe_lines(self._probe(unknown), full=True)))
        self.assertFalse(any("Скорость ответа" in line for line in report_text.probe_lines(self._probe(None), full=True)))

    def test_road_and_tag_come_from_the_verdict(self) -> None:
        pause = tp.judge(_samples(9, 42, 7, 12, 9, 9, 2_861))
        road = service_view._transfer_road(pause)

        self.assertEqual((road["key"], road["state"], road["word"]), ("transfer", "warn", "с паузами"))
        fast = service_view._transfer_road(tp.judge(_samples(20, 25, 30)))
        self.assertEqual((fast["state"], fast["word"]), ("ok", "быстро"))

        class _Service:
            control = False

        tags = service_view.service_tags(_Service(), [self._probe(pause)])
        self.assertIn(("pause", "warn"), [(tag["key"], tag["state"]) for tag in tags])
        self.assertFalse([tag for tag in service_view.service_tags(_Service(), [self._probe(tp.judge(_samples(20, 25, 30)))]) if tag["key"] == "pause"])


class CardTransferTests(unittest.TestCase):
    def test_card_shows_the_row_with_the_verdict_level(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        verdict = tp.judge(_samples(9, 42, 7, 12, 9, 9, 2_861)).as_dict()
        report["services"][0]["targets"][0]["transfer"] = verdict
        cards = build_cards(report_with_words(report))
        rows = [
            line
            for card in cards
            for section in getattr(card, "sections", ())
            for line in section.lines
            if line.name == "Скорость ответа"
        ]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, "warn")
        self.assertIn("2.9 с", rows[0].text)

    def test_old_report_without_the_field_has_no_row(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cards = build_cards(report_with_words(report))

        self.assertFalse(
            [line for card in cards for section in getattr(card, "sections", ()) for line in section.lines if line.name == "Скорость ответа"]
        )


if __name__ == "__main__":
    unittest.main()
