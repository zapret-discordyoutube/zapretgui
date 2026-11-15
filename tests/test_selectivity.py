"""Избирательность блокировки: таблица «имя × адрес × протокол» с контролями и выводы по ней."""

from __future__ import annotations

import unittest
from concurrent.futures import Future

from diagnostics import selectivity as sel
from diagnostics.selectivity import ANSWERED, CUT, FLAKY, NO_CONNECT, UNKNOWN, Cell
from utils.socket_cancel import SocketCancel

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
    def test_every_name_and_all_controls_are_measured_on_every_address(self) -> None:
        asked: list[tuple[str, str]] = []

        def probe(name: str, ip: str) -> Cell:
            asked.append((name, ip))
            return Cell(ANSWERED, ANSWERED)

        facts = _facts(probe)

        # Три имени и три контроля: постороннее имя, без имени и первое имя другими буквами.
        self.assertEqual(len(asked), (3 + 3) * 3)
        self.assertIn((sel.CONTROL_NAME, "10.0.0.2"), asked)
        self.assertIn(("", "10.0.0.3"), asked)
        self.assertIn(("www.youtube.COM", "10.0.0.1"), asked)
        self.assertEqual(len(facts.cells), 18)

    def test_stop_returns_a_cancelled_table_that_judges_to_nothing(self) -> None:
        facts = sel.collect(["a.com"], [WWW], lambda *_: Cell(), submit=_submit, should_stop=lambda: True)

        self.assertTrue(facts.cancelled)
        self.assertIsNone(sel.judge(facts))

    def test_probe_count_is_bounded(self) -> None:
        names = [f"n{i}.com" for i in range(20)]
        addresses = [(f"h{i}", f"10.0.1.{i}") for i in range(20)]

        facts = sel.collect(names, addresses, lambda *_: Cell(), submit=_submit)

        self.assertLessEqual(len(facts.cells), (sel.MAX_NAMES + 3) * sel.MAX_ADDRESSES)


class JudgeTests(unittest.TestCase):
    def test_quic_cut_for_one_name_only_is_a_name_filter_and_others_pass(self) -> None:
        # Как в отчёте: QUIC к www.youtube.com теряется на всех адресах, остальные имена и контроли проходят.
        # Фильтр регистр не различает, как и сервер: имя другими буквами режется так же.
        def rule(name: str, ip: str) -> Cell:
            return Cell(ANSWERED, CUT if name.lower() == "www.youtube.com" else ANSWERED)

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

    def test_name_cut_but_passing_in_other_letters_is_a_letter_by_letter_filter(self) -> None:
        # Как за рубежом у видеосерверов ТТК: строчное имя молчит, то же имя с заглавными COM проходит.
        def rule(name: str, ip: str) -> Cell:
            return Cell(CUT if name == "www.youtube.com" else ANSWERED, ANSWERED)

        findings = [item for item in sel.judge(_facts(rule)).findings if item.protocol == sel.PROTOCOL_TLS]
        letters = [item for item in findings if "буква в букву" in item.title]

        self.assertEqual(len(letters), 1)
        self.assertEqual(letters[0].scope, sel.SCOPE_NAME)
        self.assertIn("www.youtube.COM", letters[0].text)

    def test_other_letters_count_as_a_live_address_control(self) -> None:
        # Адрес молчит на всё, кроме имени другими буквами: он жив, закрытым его называть нельзя.
        def rule(name: str, ip: str) -> Cell:
            if ip == "10.0.0.1":
                return Cell(ANSWERED if name == "www.youtube.COM" else CUT, ANSWERED)
            return Cell(ANSWERED, ANSWERED)

        findings = _by_scope(sel.judge(_facts(rule)), sel.PROTOCOL_TLS)

        self.assertNotIn(sel.SCOPE_ADDRESS, findings)

    def test_table_has_a_row_per_name_and_all_controls(self) -> None:
        verdict = sel.judge(_facts(lambda name, ip: Cell(ANSWERED, ANSWERED)))

        self.assertEqual(len(verdict.table), 6)
        self.assertEqual(verdict.table[-3]["name"], f"{sel.CONTROL_NAME} (контроль)")
        self.assertEqual(verdict.table[-2]["name"], "без имени (контроль)")
        self.assertEqual(verdict.table[-1]["name"], "www.youtube.COM (контроль: то же имя другими буквами)")
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

        return SimpleNamespace(submit=_submit, dns_cancelled=lambda: False, probe_cancel=SocketCancel())

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
        run = SimpleNamespace(submit=_submit, dns_cancelled=lambda: False, probe_cancel=SocketCancel())
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


class RecheckTests(unittest.TestCase):
    """Одна потеря пакета — не блокировка: сочетание без ответа проверяется ещё раз."""

    def test_cell_that_answers_on_the_second_try_is_flaky_and_not_blamed_on_the_name(self) -> None:
        # Как в отчёте с МТС: i.ytimg.com на одном адресе Google в первый раз «оборвался», а на всех остальных прошёл.
        seen: dict[tuple[str, str], int] = {}

        def rule(name: str, ip: str) -> Cell:
            seen[(name, ip)] = seen.get((name, ip), 0) + 1
            if (name, ip) == ("i.ytimg.com", "10.0.0.1") and seen[(name, ip)] == 1:
                return Cell(CUT, ANSWERED)
            return Cell(ANSWERED, ANSWERED)

        facts = _facts(rule)
        verdict = sel.judge(facts)
        tls = _by_scope(verdict, sel.PROTOCOL_TLS)

        self.assertEqual(facts.cells[("i.ytimg.com", "10.0.0.1")].tls, FLAKY)
        self.assertEqual(facts.cells[("i.ytimg.com", "10.0.0.1")].quic, ANSWERED)
        self.assertEqual(set(tls), {sel.SCOPE_NONE, sel.SCOPE_FLAKY})
        self.assertIn("i.ytimg.com — на www.youtube.com (10.0.0.1)", tls[sel.SCOPE_FLAKY].text)
        self.assertEqual(tls[sel.SCOPE_FLAKY].level, "info")

    def test_only_cells_without_an_answer_are_probed_again(self) -> None:
        asked: list[tuple[str, str]] = []

        def rule(name: str, ip: str) -> Cell:
            asked.append((name, ip))
            if name == "www.youtube.com":
                return Cell(ANSWERED, CUT)
            if name == "i.ytimg.com" and ip == "10.0.0.2":
                return Cell(NO_CONNECT, ANSWERED)
            return Cell(ANSWERED, ANSWERED)

        _facts(rule)

        # 18 сочетаний в первый заход, затем ещё раз — 3 обрыва QUIC и одно «нет соединения».
        self.assertEqual(len(asked), 18 + 4)
        self.assertEqual(asked[18:].count(("www.youtube.com", "10.0.0.1")), 1)
        self.assertIn(("i.ytimg.com", "10.0.0.2"), asked[18:])

    def test_cut_twice_is_still_a_cut_by_name(self) -> None:
        facts = _facts(lambda name, ip: Cell(ANSWERED, CUT if name.lower() == "www.youtube.com" else ANSWERED))
        quic = _by_scope(sel.judge(facts), sel.PROTOCOL_QUIC)

        self.assertEqual(facts.cells[("www.youtube.com", "10.0.0.1")].quic, CUT)
        self.assertEqual(set(quic), {sel.SCOPE_NAME})

    def test_no_recheck_after_stop(self) -> None:
        asked: list[tuple[str, str]] = []
        stops = iter([False] * 18 + [True] * 100)

        def rule(name: str, ip: str) -> Cell:
            asked.append((name, ip))
            return Cell(CUT, CUT)

        facts = sel.collect(
            ["www.youtube.com", "i.ytimg.com", "rr1.googlevideo.com"],
            [WWW, IMG, NODE],
            rule,
            submit=_submit,
            should_stop=lambda: next(stops),
        )

        self.assertEqual(len(asked), 18)
        self.assertTrue(facts.cancelled)


class CacheWithoutQuicTests(unittest.TestCase):
    """Кэш провайдера отвечает по TLS только на своё имя и QUIC не поддерживает: это не «закрытый адрес»."""

    def test_quic_silent_node_that_answers_tls_is_info_not_closed(self) -> None:
        # Отчёт с МТС (270): rr4 (212.188.37.143) — TLS отвечает только своему имени, QUIC молчит на всё.
        def rule(name: str, ip: str) -> Cell:
            if ip == "10.0.0.3":
                return Cell(ANSWERED if name == "rr1.googlevideo.com" else CUT, CUT)
            return Cell(ANSWERED, ANSWERED)

        quic = [item for item in sel.judge(_facts(rule)).findings if item.protocol == sel.PROTOCOL_QUIC]

        self.assertEqual([(item.scope, item.level) for item in quic], [(sel.SCOPE_ADDRESS, "info")])
        self.assertIn("узел без QUIC", quic[0].title)
        self.assertIn("rr1.googlevideo.com (10.0.0.3)", quic[0].text)
        self.assertNotIn("Закрыт сам адрес", quic[0].text)

    def test_node_silent_on_both_protocols_is_still_closed(self) -> None:
        # Как 172.217.114.4 в том же отчёте: не отвечает ни TLS, ни QUIC — это закрытый адрес.
        def rule(name: str, ip: str) -> Cell:
            return Cell(CUT, CUT) if ip == "10.0.0.2" else Cell(ANSWERED, ANSWERED)

        verdict = sel.judge(_facts(rule))
        for protocol in (sel.PROTOCOL_TLS, sel.PROTOCOL_QUIC):
            with self.subTest(protocol=protocol):
                closed = _by_scope(verdict, protocol)[sel.SCOPE_ADDRESS]
                self.assertEqual(closed.level, "warn")
                self.assertIn("закрыт адрес", closed.title)


class LossyAddressTests(unittest.TestCase):
    """Если на адресе пропадает и постороннее имя, имя сайта в этом не виновато."""

    def test_names_lost_where_the_neutral_name_is_lost_too_prove_nothing(self) -> None:
        # Отчёт с МТС (проверка домена): на 142.251.156.4 QUIC пропал у имён сервиса и у example.com,
        # а без имени и другими буквами ответил. Раньше это давало «режут по имени» и «буква в букву».
        def rule(name: str, ip: str) -> Cell:
            if ip == "10.0.0.1" and name in ("www.youtube.com", "i.ytimg.com", sel.CONTROL_NAME):
                return Cell(ANSWERED, CUT)
            return Cell(ANSWERED, ANSWERED)

        quic = [item for item in sel.judge(_facts(rule)).findings if item.protocol == sel.PROTOCOL_QUIC]

        self.assertNotIn(sel.SCOPE_NAME, {item.scope for item in quic})
        self.assertFalse([item for item in quic if "буква в букву" in item.title])
        self.assertNotIn(sel.SCOPE_NODE, {item.scope for item in quic})

    def test_neutral_name_answering_keeps_the_address_alive(self) -> None:
        def rule(name: str, ip: str) -> Cell:
            if ip == "10.0.0.1" and name in ("www.youtube.com", ""):
                return Cell(ANSWERED, CUT)
            return Cell(ANSWERED, ANSWERED)

        quic = _by_scope(sel.judge(_facts(rule)), sel.PROTOCOL_QUIC)

        self.assertIn(sel.SCOPE_NAME, quic)
        self.assertEqual(quic[sel.SCOPE_NAME].blocked, ("www.youtube.com",))

    def test_unknown_neutral_name_falls_back_to_the_other_controls(self) -> None:
        def rule(name: str, ip: str) -> Cell:
            if ip == "10.0.0.1" and name == sel.CONTROL_NAME:
                return Cell(ANSWERED, UNKNOWN)
            if ip == "10.0.0.1" and name == "www.youtube.com":
                return Cell(ANSWERED, CUT)
            return Cell(ANSWERED, ANSWERED)

        quic = _by_scope(sel.judge(_facts(rule)), sel.PROTOCOL_QUIC)

        self.assertIn(sel.SCOPE_NAME, quic)


class CellProbeTests(unittest.TestCase):
    """Ячейка таблицы: снятая проба QUIC — не обрыв, неоткрывшееся соединение TCP — отдельный исход."""

    def _cell(self, hello_kind: str, quic_ms, *, cancelled: bool):
        from types import SimpleNamespace
        from unittest.mock import patch

        from diagnostics import block_cause, quic_probe, sections

        cancel = SocketCancel()
        if cancelled:
            cancel.cancel()
        run = SimpleNamespace(submit=_submit, probe_cancel=cancel)
        hello = lambda ip, name, **_kw: block_cause.HelloResult(hello_kind)
        with patch.object(block_cause, "tls_hello", hello), patch.object(quic_probe, "quic_hello", lambda *a, **k: quic_ms):
            return sections._matrix_cell(run)("www.youtube.com", "10.0.0.1")

    def test_cancelled_quic_is_unknown_not_cut(self) -> None:
        self.assertEqual(self._cell("cancelled", None, cancelled=True).quic, UNKNOWN)

    def test_silent_quic_without_cancel_is_cut(self) -> None:
        self.assertEqual(self._cell("timeout", None, cancelled=False).quic, CUT)

    def test_tcp_that_never_opened_is_no_connect(self) -> None:
        cell = self._cell("connect", 30.0, cancelled=False)

        self.assertEqual(cell.tls, NO_CONNECT)
        self.assertEqual(cell.quic, ANSWERED)

    def test_table_words_for_flaky_and_no_connect(self) -> None:
        from diagnostics import service_view

        matrix = {
            "addresses": [{"host": "www.youtube.com", "ip": "10.0.0.1"}],
            "table": [{"name": "www.youtube.com", "cells": [{"ip": "10.0.0.1", "tls": "no_connect", "quic": "flaky"}]}],
        }

        self.assertIn("TLS нет соединения, QUIC через раз", service_view._matrix_text(matrix))


if __name__ == "__main__":
    unittest.main()
