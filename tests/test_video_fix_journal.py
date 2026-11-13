"""Журнал исправлений видео: программа учится на результате в сети человека, а не угадывает."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from diagnostics import block_cause, problems, video_fix_journal as journal, video_sweep
from diagnostics.block_cause import HelloResult
from diagnostics.video_sweep import SweepFacts
from settings.normalize import normalize_video_fix_journal
from utils.ip_owner import IpOwner

_PROVIDER = IpOwner(asn="31133", owner="MegaFon")
_GOOGLE = IpOwner(asn="15169", owner="GOOGLE")


def _facts(host: str, hello: str, owner) -> SweepFacts:
    return SweepFacts(host, "203.0.113.5", video_sweep.FAMILY_V4, HelloResult(hello), None, owner)


def _collected(*facts: SweepFacts) -> dict:
    probe = SimpleNamespace(target=SimpleNamespace(discover_googlevideo=True), video_sweep=tuple(facts), quic=None)
    return {"youtube": [probe]}


_CACHE_SILENT = _facts("rr1---sn-hxb54vo-3046.googlevideo.com", block_cause.HELLO_TIMEOUT, _PROVIDER)
_CACHE_OK = _facts("rr1---sn-hxb54vo-3046.googlevideo.com", block_cause.HELLO_OK, _PROVIDER)
_GOOGLE_OK = _facts("rr2---sn-aj5go5-5i.googlevideo.com", block_cause.HELLO_OK, _GOOGLE)
_GOOGLE_SILENT = _facts("rr2---sn-aj5go5-5i.googlevideo.com", block_cause.HELLO_TIMEOUT, _GOOGLE)


def _entry(action: str = "youtube_block_caches", *, answering: int = 1, total: int = 2, network: str = "AS31133", verdict: str = "") -> dict:
    return {"action": action, "target": "sn-hxb54vo-3046", "time": "2026-11-13T10:00:00", "network": network,
            "before": {"answering": answering, "total": total}, "verdict": verdict}


def _titles(rows: list[dict]) -> list[str]:
    return [row["title"] for row in rows]


class PureTests(unittest.TestCase):
    def test_summary_and_network_from_the_sweep(self) -> None:
        sweep = [node.as_dict() for node in video_sweep.judge((_CACHE_SILENT, _GOOGLE_OK))]
        self.assertEqual(journal.summary(sweep), {"answering": 1, "total": 2})
        self.assertEqual(journal.network_key(sweep), "AS31133")

    def test_verdict_by_share_of_answering_servers(self) -> None:
        self.assertEqual(journal.judge({"answering": 1, "total": 2}, {"answering": 2, "total": 2}), journal.VERDICT_HELPED)
        self.assertEqual(journal.judge({"answering": 2, "total": 2}, {"answering": 1, "total": 3}), journal.VERDICT_WORSE)
        self.assertEqual(journal.judge({"answering": 1, "total": 2}, {"answering": 2, "total": 4}), journal.VERDICT_SAME)
        self.assertEqual(journal.judge({"answering": 1, "total": 2}, {"answering": 0, "total": 0}), "")

    def test_failed_fix_is_remembered_per_network(self) -> None:
        log = [_entry(verdict=journal.VERDICT_WORSE)]
        self.assertTrue(journal.tried_without_luck(log, "youtube_block_caches", "AS31133"))
        self.assertFalse(journal.tried_without_luck(log, "youtube_block_caches", "AS8359"))
        self.assertFalse(journal.tried_without_luck([_entry(verdict=journal.VERDICT_HELPED)], "youtube_block_caches", "AS31133"))

    def test_sweep_is_found_in_a_report_with_services_as_list_or_dict(self) -> None:
        node = {"code": "ok"}
        as_list = {"services": [{"targets": [{"video_sweep": [node]}]}]}
        as_dict = {"services": {"youtube": {"targets": [{"video_sweep": [node, node]}]}}}
        self.assertEqual(len(journal.sweep_from_report(as_list)), 1)
        self.assertEqual(len(journal.sweep_from_report(as_dict)), 2)
        self.assertEqual(journal.sweep_from_report(None), [])


class ProblemRowsTests(unittest.TestCase):
    def test_fresh_network_gets_the_offer_with_the_button(self) -> None:
        rows = problems.video_fix_problems(_collected(_CACHE_SILENT, _GOOGLE_OK))
        self.assertEqual([row["action"] for row in rows], ["youtube_block_caches"])
        self.assertEqual(rows[0]["target"], "sn-hxb54vo-3046")

    def test_helped_is_said_after_the_next_check(self) -> None:
        rows = problems.video_fix_problems(_collected(_CACHE_OK, _GOOGLE_OK), journal=[_entry()])
        self.assertIn("Исправление видео помогло", _titles(rows))

    def test_worse_after_blocking_offers_the_undo_button(self) -> None:
        rows = problems.video_fix_problems(_collected(_CACHE_SILENT, _GOOGLE_SILENT), journal=[_entry()])
        worse = next(row for row in rows if row["title"] == "Исправление видео не помогло")
        self.assertEqual(worse["action"], "youtube_unblock_caches")

    def test_fix_that_did_not_help_here_is_not_offered_again(self) -> None:
        rows = problems.video_fix_problems(_collected(_CACHE_SILENT, _GOOGLE_OK), journal=[_entry(verdict=journal.VERDICT_SAME)])
        self.assertNotIn("youtube_block_caches", [row["action"] for row in rows])

    def test_lesson_of_another_network_does_not_apply(self) -> None:
        rows = problems.video_fix_problems(
            _collected(_CACHE_SILENT, _GOOGLE_OK), journal=[_entry(network="AS8359", verdict=journal.VERDICT_WORSE)]
        )
        self.assertIn("youtube_block_caches", [row["action"] for row in rows])

    def test_pending_fix_from_another_network_is_not_judged(self) -> None:
        entry, verdict, _after = problems.journal_verdict(_collected(_CACHE_OK, _GOOGLE_OK), [_entry(network="AS8359")])
        self.assertIsNotNone(entry)
        self.assertEqual(verdict, "")

    def test_blocked_caches_offer_to_bring_them_back(self) -> None:
        rows = problems.video_fix_problems({}, cache_block=("sn-hxb54vo-3046",))
        self.assertEqual([row["action"] for row in rows], ["youtube_unblock_caches"])


class NormalizeTests(unittest.TestCase):
    def test_only_known_actions_and_sane_counts_are_kept(self) -> None:
        raw = [
            _entry(),
            {"action": "rm -rf", "time": "2026-11-13T10:00:00"},
            {**_entry(), "before": {"answering": -5, "total": "x"}, "verdict": "maybe"},
        ]
        clean = normalize_video_fix_journal(raw)
        self.assertEqual(len(clean), 2)
        self.assertEqual(clean[1]["before"], {"answering": 0, "total": 0})
        self.assertEqual(clean[1]["verdict"], "")


if __name__ == "__main__":
    unittest.main()
