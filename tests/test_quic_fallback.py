"""Цена отката с QUIC на TCP: вывод по открытиям настоящим браузером без самого браузера."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from blockcheck import browser_check
from diagnostics import block_view, sections
from diagnostics import quic_fallback as qf
from diagnostics.verdict import ReachState


def _visit(ms: float, state: str = browser_check.BROWSER_OK, text: str = "") -> browser_check.BrowserVisit:
    return browser_check.BrowserVisit(state, text=text, browser="msedge.exe", elapsed_ms=ms)


def _facts(quic, tcp) -> qf.FallbackFacts:
    return qf.FallbackFacts("youtube.com", tuple(quic), tuple(tcp))


class JudgeTests(unittest.TestCase):
    def test_quic_blackhole_costs_seconds_and_the_median_is_used(self) -> None:
        verdict = qf.judge(_facts([_visit(4800), _visit(5200), _visit(9000)], [_visit(1500), _visit(1700)]))

        self.assertEqual(verdict.code, qf.FALLBACK_COST)
        self.assertEqual((round(verdict.quic_ms), round(verdict.tcp_ms)), (5200, 1600))
        self.assertIn("около 3.6 с", verdict.text)

    def test_difference_inside_the_noise_is_no_cost(self) -> None:
        verdict = qf.judge(_facts([_visit(1700), _visit(1800)], [_visit(1500), _visit(1600)]))

        self.assertEqual(verdict.code, qf.FALLBACK_NONE)
        self.assertIn("почти ничего не стоит", verdict.text)

    def test_quic_faster_is_not_a_negative_cost(self) -> None:
        verdict = qf.judge(_facts([_visit(900)], [_visit(1500)]))

        self.assertEqual((verdict.code, verdict.penalty_ms), (qf.FALLBACK_NONE, 0.0))

    def test_page_that_opens_only_without_quic_is_broken(self) -> None:
        failed = _visit(0, browser_check.BROWSER_FAILED, "соединение сброшено")
        verdict = qf.judge(_facts([failed, failed], [_visit(1500)]))

        self.assertEqual(verdict.code, qf.FALLBACK_BROKEN)
        self.assertIn("соединение сброшено", verdict.text)

    def test_browser_that_says_nothing_with_quic_is_unknown_not_broken(self) -> None:
        silent = _visit(0.0, browser_check.BROWSER_NO_ANSWER)
        verdict = qf.judge(qf.FallbackFacts("x.com", (silent, silent), (_visit(1000), _visit(1100))))
        self.assertEqual(verdict.code, qf.FALLBACK_UNKNOWN)

    def test_no_browser_or_no_tcp_result_is_unknown(self) -> None:
        missing = browser_check.BrowserVisit(browser_check.BROWSER_MISSING, text="нет браузера")
        self.assertEqual(qf.judge(_facts([missing], [missing])).code, qf.FALLBACK_UNKNOWN)
        self.assertEqual(qf.judge(_facts([], [])).code, qf.FALLBACK_UNKNOWN)
        failed = _visit(0, browser_check.BROWSER_FAILED)
        self.assertEqual(qf.judge(_facts([_visit(1000)], [failed])).code, qf.FALLBACK_UNKNOWN)


class CollectTests(unittest.TestCase):
    def test_site_is_opened_alternately_with_and_without_quic(self) -> None:
        order: list[bool] = []

        def visit(_host, *, quic, cancelled):
            order.append(quic)
            return _visit(1000)

        facts = qf.collect("youtube.com", visit=visit)

        self.assertEqual(order, [True, False, True, False])
        self.assertEqual((len(facts.with_quic), len(facts.without_quic)), (2, 2))

    def test_stop_ends_the_collection(self) -> None:
        calls: list[bool] = []
        facts = qf.collect("a.com", visit=lambda *_a, **_k: calls.append(True) or _visit(1), cancelled=lambda: True)

        self.assertEqual((calls, facts.with_quic, facts.without_quic), ([], (), ()))


class SectionTests(unittest.TestCase):
    @staticmethod
    def _probe(host: str, *, quic: str | None, state=ReachState.OK, main: bool = True):
        return SimpleNamespace(
            host=host, target=SimpleNamespace(main=main), reach_state=state, quic=None if quic is None else SimpleNamespace(code=quic)
        )

    def _run(self, collected, visit):
        services = {"yt": SimpleNamespace(control=False, label="YouTube"), "ya": SimpleNamespace(control=True, label="Яндекс")}
        run = SimpleNamespace(dns_cancelled=lambda: False)
        return sections.check_quic_fallback(run, collected, services, visit=visit)

    def test_a_site_with_blocked_quic_is_measured(self) -> None:
        calls: list[str] = []

        def visit(host, *, quic, cancelled):
            calls.append(host)
            return _visit(5000 if quic else 1500)

        result = self._run({"yt": [self._probe("www.youtube.com", quic="blocked_by_name")], "ya": []}, visit)

        self.assertEqual((result["code"], result["host"], result["label"]), ("cost", "www.youtube.com", "YouTube"))
        self.assertEqual(set(calls), {"www.youtube.com"})

    def test_nothing_to_measure_without_a_blocked_quic_or_when_the_site_is_closed(self) -> None:
        never = lambda *_a, **_k: self.fail("браузер не должен запускаться")  # noqa: E731
        self.assertIsNone(self._run({"yt": [self._probe("a", quic="ok")]}, never))
        self.assertIsNone(self._run({"yt": [self._probe("a", quic=None)]}, never))
        self.assertIsNone(self._run({"yt": [self._probe("a", quic="blocked_by_name", state=ReachState.DPI)]}, never))
        self.assertIsNone(self._run({"yt": [self._probe("a", quic="silent", main=False)]}, never))


class BlockTests(unittest.TestCase):
    DATA = {"code": "cost", "text": "откат стоит около 3.6 с", "host": "www.youtube.com", "label": "YouTube", "quic_ms": 5200, "tcp_ms": 1600}

    def test_block_has_the_verdict_the_numbers_and_the_advice(self) -> None:
        view = block_view.build_block("quic_fallback", self.DATA)

        self.assertEqual((view.title, view.level, view.status), ("Откат QUIC", "warn", "Стоит времени"))
        titles = [section.title for section in view.sections]
        self.assertEqual(titles[:3], ["Вывод", "Что измеряли", "Что делать"])
        text = "\n".join(block_view.text_lines("quic_fallback", self.DATA))
        self.assertIn("Браузер с QUIC: 5.2 с", text)
        self.assertIn("правилом drop для UDP 443", text)

    def test_no_cost_has_no_advice(self) -> None:
        view = block_view.build_block("quic_fallback", {"code": "none", "text": "почти ничего не стоит"})

        self.assertFalse(any(section.title == "Что делать" for section in view.sections))
        self.assertEqual(view.level, "ok")


class BrowserTimeTests(unittest.TestCase):
    def test_visit_carries_how_long_the_browser_took(self) -> None:
        ticks = iter([10.0, 10.0, 12.5, 12.5, 12.5, 12.5])

        class _Process:
            def poll(self):
                return 0

            def kill(self):
                pass

            def wait(self):
                pass

        def popen(command, output):
            output.write(b"<html><body>ok</body></html>")
            return _Process()

        visit = browser_check.open_in_browser(
            "example.com", popen=popen, browser=r"C:\Edge\msedge.exe", clock=lambda: next(ticks, 12.5)
        )

        self.assertTrue(visit.ok)
        self.assertEqual(round(visit.elapsed_ms), 2500)


if __name__ == "__main__":
    unittest.main()
