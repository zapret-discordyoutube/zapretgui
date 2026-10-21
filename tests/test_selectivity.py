"""Избирательность блокировки: таблица «имя × адрес × протокол» с контролями и выводы по ней."""

from __future__ import annotations

import unittest
from concurrent.futures import Future

from diagnostics import selectivity as sel
from diagnostics.selectivity import ANSWERED, CUT, Cell

WWW = ("www.youtube.com", "10.0.0.1")
IMG = ("i.ytimg.com", "10.0.0.2")
NODE = ("rr1.googlevideo.com", "10.0.0.3")


def _submit(fn, *args, **kwargs) -> Future:
    future: Future = Future()
    future.set_result(fn(*args, **kwargs))
    return future


def _facts(rule) -> sel.MatrixFacts:
    """``rule(name, ip) -> Cell`` задаёт, как фильтр отвечает на каждое сочетание."""
    return sel.collect(
        ["www.youtube.com", "i.ytimg.com", "rr1.googlevideo.com"], [WWW, IMG, NODE], rule, submit=_submit
    )


def _by_scope(verdict: sel.MatrixVerdict, protocol: str) -> dict[str, sel.Finding]:
    return {item.scope: item for item in verdict.findings if item.protocol == protocol}


class CollectTests(unittest.TestCase):
    def test_every_name_and_both_controls_are_measured_on_every_address(self) -> None:
        asked: list[tuple[str, str]] = []

        def probe(name: str, ip: str) -> Cell:
            asked.append((name, ip))
            return Cell(ANSWERED, ANSWERED)

        facts = _facts(probe)

        self.assertEqual(len(asked), (3 + 2) * 3)
        self.assertIn((sel.CONTROL_NAME, "10.0.0.2"), asked)
        self.assertIn(("", "10.0.0.3"), asked)
        self.assertEqual(len(facts.cells), 15)

    def test_stop_returns_a_cancelled_table_that_judges_to_nothing(self) -> None:
        facts = sel.collect(["a.com"], [WWW], lambda *_: Cell(), submit=_submit, should_stop=lambda: True)

        self.assertTrue(facts.cancelled)
        self.assertIsNone(sel.judge(facts))

    def test_probe_count_is_bounded(self) -> None:
        names = [f"n{i}.com" for i in range(20)]
        addresses = [(f"h{i}", f"10.0.1.{i}") for i in range(20)]

        facts = sel.collect(names, addresses, lambda *_: Cell(), submit=_submit)

        self.assertLessEqual(len(facts.cells), (sel.MAX_NAMES + 2) * sel.MAX_ADDRESSES)


class JudgeTests(unittest.TestCase):
    def test_quic_cut_for_one_name_only_is_a_name_filter_and_others_pass(self) -> None:
        # Как в отчёте: QUIC к www.youtube.com теряется на всех адресах, остальные имена и контроли проходят.
        def rule(name: str, ip: str) -> Cell:
            return Cell(ANSWERED, CUT if name == "www.youtube.com" else ANSWERED)

        verdict = sel.judge(_facts(rule))
        quic = _by_scope(verdict, sel.PROTOCOL_QUIC)
        tls = _by_scope(verdict, sel.PROTOCOL_TLS)

        self.assertEqual(set(quic), {sel.SCOPE_NAME})
        self.assertEqual(quic[sel.SCOPE_NAME].blocked, ("www.youtube.com",))
        self.assertEqual(set(quic[sel.SCOPE_NAME].passing), {"i.ytimg.com", "rr1.googlevideo.com"})
        self.assertIn("избирательна", quic[sel.SCOPE_NAME].text)
        self.assertEqual(set(tls), {sel.SCOPE_NONE})

    def test_same_name_cut_on_one_node_only_is_node_dependent(self) -> None:
        def rule(name: str, ip: str) -> Cell:
            return Cell(CUT if (name == "rr1.googlevideo.com" and ip == "10.0.0.3") else ANSWERED, ANSWERED)

        findings = _by_scope(sel.judge(_facts(rule)), sel.PROTOCOL_TLS)

        self.assertIn(sel.SCOPE_NODE, findings)
        self.assertIn("10.0.0.3", findings[sel.SCOPE_NODE].text)
        self.assertIn("зависит от узла", findings[sel.SCOPE_NODE].title)

    def test_address_that_answers_nothing_while_others_answer_is_closed(self) -> None:
        def rule(name: str, ip: str) -> Cell:
            return Cell(CUT, ANSWERED) if ip == "10.0.0.2" else Cell(ANSWERED, ANSWERED)

        findings = _by_scope(sel.judge(_facts(rule)), sel.PROTOCOL_TLS)

        self.assertEqual(set(findings), {sel.SCOPE_ADDRESS})
        self.assertIn("i.ytimg.com (10.0.0.2)", findings[sel.SCOPE_ADDRESS].text)

    def test_protocol_that_answers_nowhere_is_not_blamed_on_the_name(self) -> None:
        verdict = sel.judge(_facts(lambda name, ip: Cell(ANSWERED, CUT)))
        quic = _by_scope(verdict, sel.PROTOCOL_QUIC)

        self.assertEqual(set(quic), {sel.SCOPE_ADDRESS})
        self.assertEqual(quic[sel.SCOPE_ADDRESS].level, "info")
        self.assertIn("не блокировка по имени", quic[sel.SCOPE_ADDRESS].text)

    def test_table_has_a_row_per_name_and_both_controls(self) -> None:
        verdict = sel.judge(_facts(lambda name, ip: Cell(ANSWERED, ANSWERED)))

        self.assertEqual(len(verdict.table), 5)
        self.assertEqual(verdict.table[-2]["name"], f"{sel.CONTROL_NAME} (контроль)")
        self.assertEqual(verdict.table[-1]["name"], "без имени (контроль)")
        self.assertEqual(len(verdict.table[0]["cells"]), 3)
        self.assertEqual(verdict.as_dict()["addresses"][0], {"host": "www.youtube.com", "ip": "10.0.0.1"})


class TriggerTests(unittest.TestCase):
    def test_matrix_runs_only_on_a_trouble_signal(self) -> None:
        self.assertFalse(sel.needs_matrix(["ok", ""], ["", ""], ["", ""]))
        self.assertTrue(sel.needs_matrix(["blocked_by_name"], [], []))
        self.assertTrue(sel.needs_matrix([], ["sni"], []))
        self.assertTrue(sel.needs_matrix([], [], ["открывается через раз"]))


class AnnotateTests(unittest.TestCase):
    """Сервис с сигналом беды получает таблицу, спокойный — нет; пробы идут настоящими функциями ячейки."""

    def _probe(self, host: str, ip: str, *, main: bool, quic: str = ""):
        from types import SimpleNamespace

        return SimpleNamespace(
            host=host,
            reach=SimpleNamespace(ip=ip),
            target=SimpleNamespace(main=main),
            quic=SimpleNamespace(code=quic) if quic else None,
            cause=None,
            unstable="",
            tried=(),
        )

    def _run(self):
        from types import SimpleNamespace

        return SimpleNamespace(submit=_submit, dns_cancelled=lambda: False, probe_cancel=None)

    def test_trouble_service_gets_the_table_and_a_quiet_one_does_not(self) -> None:
        from unittest.mock import patch

        from diagnostics import block_cause, quic_probe, sections

        troubled = [self._probe("www.youtube.com", "10.0.0.1", main=True, quic="blocked_by_name"), self._probe("i.ytimg.com", "10.0.0.2", main=False, quic="ok")]
        quiet = [self._probe("a.com", "10.0.1.1", main=True, quic="ok")]
        collected = {"youtube": troubled, "calm": quiet}
        services = {"youtube": type("S", (), {"control": False})(), "calm": type("S", (), {"control": False})()}
        report = [{"key": "youtube"}, {"key": "calm"}]

        def quic(ip, name, **_kw):
            return None if name == "www.youtube.com" else 30.0

        hello = lambda ip, name, **_kw: block_cause.HelloResult(block_cause.HELLO_OK, "", 20.0)
        with patch.object(block_cause, "tls_hello", hello), patch.object(quic_probe, "quic_hello", quic):
            sections.annotate_selectivity(self._run(), report, collected, services)

        self.assertNotIn("selectivity", report[1])
        findings = {(item["protocol"], item["scope"]) for item in report[0]["selectivity"]["findings"]}
        self.assertIn(("quic", "name"), findings)
        self.assertIn(("tls", "none"), findings)

        from diagnostics import service_view

        titles = [section.title for section in service_view.service_sections(report[0])]
        self.assertIn("Таблица: имя × адрес (исход TLS и QUIC)", titles)
        text = "\n".join(service_view.render_text(service_view.service_sections(report[0])))
        self.assertIn("    на www.youtube.com 10.0.0.1 → TLS ответ, QUIC обрыв", text)


class MixedAddressesTests(unittest.TestCase):
    def test_reset_on_some_addresses_only_gets_the_table_with_those_addresses(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from diagnostics import block_cause, quic_probe, sections

        probe = SimpleNamespace(
            host="www.youtube.com",
            reach=SimpleNamespace(ip="10.0.0.3"),
            target=SimpleNamespace(main=True),
            quic=SimpleNamespace(code="ok"),
            cause=None,
            unstable="",
            tried=(("10.0.0.1", "reset"), ("10.0.0.3", "ok")),
        )
        run = SimpleNamespace(submit=_submit, dns_cancelled=lambda: False, probe_cancel=None)
        report = [{"key": "youtube"}]

        def hello(ip, name, **_kw):
            if ip == "10.0.0.1" and name == "www.youtube.com":
                return block_cause.HelloResult(block_cause.HELLO_RESET)
            return block_cause.HelloResult(block_cause.HELLO_OK, "", 20.0)

        with patch.object(block_cause, "tls_hello", hello), patch.object(quic_probe, "quic_hello", lambda *a, **k: 30.0):
            sections.annotate_selectivity(run, report, {"youtube": [probe]}, {"youtube": SimpleNamespace(control=False)})

        data = report[0]["selectivity"]
        self.assertEqual({item["ip"] for item in data["addresses"]}, {"10.0.0.1", "10.0.0.3"})
        scopes = {(item["protocol"], item["scope"]) for item in data["findings"]}
        self.assertIn(("tls", "node"), scopes)


if __name__ == "__main__":
    unittest.main()
