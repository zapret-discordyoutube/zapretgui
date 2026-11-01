"""Обрыв «на 16 КБ»: одно правило (``diagnostics.cut_window``) у всех проверок BlockCheck и у подбора."""

from __future__ import annotations

import unittest

from blockcheck.strategy_search import verdict as rules
from diagnostics import cut_window, volume_probe
from diagnostics.freeze_check import FreezeState, classify_download
from diagnostics.tls_probe import KIND_OK, ProbeResult
from diagnostics.verdict import ReachState, judge_reach

# Как у google.com с win10 (замер 2026-11-01): 3,6 КБ служебных байт TLS и 1,3 КБ заголовков.
HANDSHAKE = 3_639
HEADERS = 1_289


def _cut(body: int, *, measured: bool = True) -> ProbeResult:
    return ProbeResult(
        ip="1.1.1.1",
        kind=KIND_OK,
        status=200,
        body_size=body,
        body_cut=True,
        received_size=body + HEADERS if measured else 0,
        handshake_size=HANDSHAKE if measured else 0,
    )


class CutWindowTests(unittest.TestCase):
    def test_tspu_16kb_cut_counts_handshake_and_headers(self) -> None:
        # Фильтр оборвал соединение на ~16 КБ, а тела пришло меньше 12 КБ: это всё равно ТСПУ.
        result = _cut(10_500)
        self.assertEqual(cut_window.result_bytes(result), 10_500 + HEADERS + HANDSHAKE)
        self.assertEqual(cut_window.result_cut(result), cut_window.CUT_WINDOW)

    def test_unmeasured_handshake_is_estimated(self) -> None:
        self.assertEqual(cut_window.filter_bytes(9_000), 9_000 + cut_window.TLS_HANDSHAKE_ESTIMATE)
        self.assertEqual(cut_window.filter_bytes(9_000, tls=False), 9_000)
        self.assertEqual(cut_window.result_cut(_cut(9_000, measured=False)), cut_window.CUT_WINDOW)

    def test_places(self) -> None:
        self.assertEqual(cut_window.cut_place(3_000), cut_window.CUT_EARLY)
        self.assertEqual(cut_window.cut_place(12_000), cut_window.CUT_WINDOW)
        self.assertEqual(cut_window.cut_place(36_000), cut_window.CUT_WINDOW)
        self.assertEqual(cut_window.cut_place(36_001), cut_window.CUT_LATE)
        self.assertEqual(cut_window.result_cut(ProbeResult(ip="1.1.1.1", kind=KIND_OK, body_size=3_000)), "")

    def test_every_section_reaches_the_same_verdict(self) -> None:
        # Тот же ответ: проверка сайтов, общая проверка обрыва и подбор говорят одно.
        window, early, late = _cut(10_500), _cut(1_000), _cut(40_000)

        self.assertEqual(judge_reach(window), ReachState.FREEZE)
        self.assertEqual(classify_download(window)[0], FreezeState.FREEZE)
        self.assertIn("по объёму", rules.judge_https(window).reason)

        self.assertEqual(judge_reach(early), ReachState.DPI)
        self.assertEqual(classify_download(early)[0], FreezeState.UNKNOWN)
        outcome = rules.judge_https(early)
        self.assertEqual(outcome.state, rules.PROBE_BLOCKED)
        self.assertNotIn("по объёму", outcome.reason)

        self.assertEqual(judge_reach(late), ReachState.OK)
        self.assertEqual(classify_download(late)[0], FreezeState.OK)
        self.assertTrue(rules.judge_https(late).ok)

    def test_volume_probe_counts_like_the_filter(self) -> None:
        run = volume_probe.VolumeRun(volume_probe.RUN_STALLED, 10_500, 6, handshake=HANDSHAKE)
        self.assertEqual(volume_probe.filter_bytes(run), 10_500 + HANDSHAKE)
        self.assertEqual(volume_probe.judge(volume_probe.VolumeFacts(run, run)).code, volume_probe.VOLUME_CUT)

    def test_short_answer_needs_volume_check_until_it_passes_the_window(self) -> None:
        short = rules.judge_https(ProbeResult(ip="1.1.1.1", kind=KIND_OK, status=200, body_size=20_000, received_size=21_000))
        self.assertTrue(rules.needs_volume(short))
        whole = rules.judge_https(ProbeResult(ip="1.1.1.1", kind=KIND_OK, status=200, body_size=34_000, received_size=35_000))
        self.assertFalse(rules.needs_volume(whole))
        self.assertTrue(cut_window.whole_past_window(ProbeResult(ip="1.1.1.1", kind=KIND_OK, body_size=34_000, received_size=35_000)))


if __name__ == "__main__":
    unittest.main()
