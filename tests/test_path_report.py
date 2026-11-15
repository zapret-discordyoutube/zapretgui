"""Путь запроса: шаги от DNS до передачи по фактам одного адреса, уровни и уверенность, без сети."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from blockcheck.ui.result_cards_model import build_cards
from blockcheck_words import report_with_words
from diagnostics import block_cause, engine, path_report, report_text
from diagnostics import protocol_probe as pp
from diagnostics import quic_probe, volume_probe
from diagnostics import transfer_probe as tp
from diagnostics.limits import HTTPS_TIMEOUT
from diagnostics.tls_probe import KIND_CERT, KIND_CONNECT, KIND_OK, KIND_RESET, KIND_TIMEOUT, ProbeResult
from diagnostics.verdict import DnsJudgement, DnsState, ReachState

S = tp.RequestSample
FIXTURE = Path(__file__).parent / "fixtures" / "blockcheck_report_sample.json"
IP = "203.0.113.5"


def _probe(**facts) -> engine._Probe:
    probe = engine._Probe(target=engine.Target("x.com", "сайт", main=True), service="x", host="x.com")
    probe.judgement = DnsJudgement(DnsState.OK, "ответ DNS совпал с эталоном")
    probe.reference_ok = True
    probe.reach = ProbeResult(ip=IP, kind=KIND_OK, status=200, body_size=500)
    probe.reach_state = ReachState.OK
    probe.tried = ((IP, "ok"),)
    for key, value in facts.items():
        setattr(probe, key, value)
    return probe


def _by_key(probe) -> dict[str, path_report.PathStep]:
    return {step.key: step for step in path_report.build(probe)}


class PathOrderTests(unittest.TestCase):
    def test_five_steps_always_in_one_order(self) -> None:
        steps = path_report.build(_probe())

        self.assertEqual([step.key for step in steps], ["dns", "tcp", "tls", "quic", "data"])
        self.assertEqual([step.title for step in steps][0], "DNS")

    def test_step_as_dict_is_complete(self) -> None:
        data = path_report.build(_probe())[0].as_dict()

        self.assertEqual(sorted(data), ["confidence", "key", "level", "text", "title"])


class HealthyPathTests(unittest.TestCase):
    def test_everything_open_has_no_breaking_step(self) -> None:
        probe = _probe(
            quic=quic_probe.QuicVerdict(quic_probe.QUIC_OK, "отвечает за 40 мс"),
            volume=volume_probe.VolumeVerdict(volume_probe.VOLUME_OK, "получено 48 КБ без обрыва"),
            transfer=tp.judge([S(ttfb_ms=300, fresh=True), *(S(received=5_000, ttfb_ms=ms) for ms in (20, 25, 30, 22, 24, 21))]),
        )
        steps = path_report.build(probe)

        self.assertIsNone(path_report.breaking_step(steps))
        self.assertEqual([step.level for step in steps], ["ok"] * 5)
        self.assertEqual(path_report.chain_text(steps), "DNS ✅ → TCP ✅ → TLS ✅ → QUIC ✅ → передача ✅")

    def test_unchecked_steps_are_unknown_and_not_a_breakage(self) -> None:
        steps = path_report.build(_probe())

        self.assertEqual({step.key: step.level for step in steps}["quic"], "unknown")
        self.assertEqual({step.key: step.level for step in steps}["data"], "unknown")
        self.assertIsNone(path_report.breaking_step(steps))


class TtkPathTests(unittest.TestCase):
    def test_handshake_passes_and_only_the_transfer_breaks(self) -> None:
        """Сбой YouTube у ТТК: соединение и шифрование в порядке, а передача идёт с паузами."""
        probe = _probe(
            volume=volume_probe.VolumeVerdict(volume_probe.VOLUME_OK, "получено 48 КБ без обрыва"),
            transfer=tp.judge([S(ttfb_ms=300, fresh=True), *(S(received=5_000, ttfb_ms=ms) for ms in (9, 42, 7, 12, 9, 9, 2_861, 2_900))]),
        )
        steps = path_report.build(probe)
        broken = path_report.breaking_step(steps)

        self.assertEqual((broken.key, broken.level), ("data", "warn"))
        self.assertEqual(broken.confidence, tp.CONFIDENCE_HIGH)
        self.assertEqual([step.level for step in steps[:3]], ["ok", "ok", "ok"])
        self.assertTrue(path_report.chain_text(steps).endswith("передача ⚠️"))


class BrokenPathTests(unittest.TestCase):
    def test_dpi_with_a_confident_cause_breaks_tls(self) -> None:
        probe = _probe(
            reach=ProbeResult(ip=IP, kind=KIND_RESET, detail="сброс"),
            reach_state=ReachState.DPI,
            cause=block_cause.Cause("by_name", "режут по имени сайта: с чужим именем сервер отвечает", True),
        )
        steps = _by_key(probe)

        self.assertEqual((steps["tcp"].level, steps["tls"].level), ("ok", "fail"))
        self.assertEqual(steps["tls"].confidence, tp.CONFIDENCE_HIGH)
        self.assertIn("Режут по имени сайта", steps["tls"].text)
        self.assertEqual(path_report.breaking_step(list(steps.values())).key, "tls")

    def test_dpi_without_a_confident_cause_is_less_certain(self) -> None:
        probe = _probe(reach=ProbeResult(ip=IP, kind=KIND_RESET), reach_state=ReachState.DPI)

        self.assertEqual(_by_key(probe)["tls"].confidence, tp.CONFIDENCE_MEDIUM)

    def test_silent_tls_names_the_real_wait_not_zero(self) -> None:
        # Отчёт с МТС на 273: строка «Соединение» писала «за 5 с», а шаг TLS того же адреса — «за 0 с».
        probe = _probe(reach=ProbeResult(ip=IP, kind=KIND_TIMEOUT), reach_state=ReachState.DPI)
        text = _by_key(probe)["tls"].text

        self.assertIn(f"не ответил за {HTTPS_TIMEOUT:.0f} с", text)
        self.assertNotIn("за 0 с", text)

    def test_closed_address_breaks_tcp_and_leaves_tls_unknown(self) -> None:
        probe = _probe(
            reach=ProbeResult(ip=IP, kind=KIND_CONNECT),
            reach_state=ReachState.IP_BLOCK,
            tried=((IP, "connect"), ("203.0.113.6", "connect")),
        )
        steps = _by_key(probe)

        self.assertEqual((steps["tcp"].level, steps["tcp"].confidence), ("fail", tp.CONFIDENCE_HIGH))
        self.assertIn("ни по одному из 2 адресов", steps["tcp"].text)
        self.assertEqual(steps["tls"].level, "unknown")

    def test_spoofed_dns_is_a_warning_on_the_first_step(self) -> None:
        probe = _probe(judgement=DnsJudgement(DnsState.SPOOFED, "DNS подменяет адрес"))
        steps = _by_key(probe)

        self.assertEqual((steps["dns"].level, steps["dns"].text), ("warn", "DNS подменяет адрес"))
        self.assertEqual(path_report.breaking_step(list(steps.values())).key, "dns")

    def test_dns_without_reference_is_less_certain(self) -> None:
        self.assertEqual(_by_key(_probe(reference_ok=False))["dns"].confidence, tp.CONFIDENCE_LOW)
        self.assertEqual(_by_key(_probe())["dns"].confidence, tp.CONFIDENCE_HIGH)

    def test_foreign_certificate_breaks_tls(self) -> None:
        probe = _probe(
            reach=ProbeResult(ip=IP, kind=KIND_CERT, cert_problem="сертификат выдан другому сайту"),
            reach_state=ReachState.CERT,
        )

        self.assertEqual(_by_key(probe)["tls"].level, "fail")

    def test_browser_fingerprint_block_is_named(self) -> None:
        line = pp.ProtocolLine("browser", "Chrome", "fail", "сброс", "сброшено", code=pp.CODE_FINGERPRINT)
        probe = _probe(protocols=(line,), reach=ProbeResult(ip=IP, kind=KIND_RESET), reach_state=ReachState.DPI)
        step = _by_key(probe)["tls"]

        self.assertEqual((step.level, step.confidence), ("fail", tp.CONFIDENCE_HIGH))
        self.assertIn("почерку", step.text)

    def test_cut_on_volume_breaks_the_data_step_not_tls(self) -> None:
        probe = _probe(
            reach_state=ReachState.FREEZE,
            volume=volume_probe.VolumeVerdict(volume_probe.VOLUME_CUT, "соединение замирает после 14 КБ", cut_kb=14),
        )
        steps = _by_key(probe)

        self.assertEqual((steps["tls"].level, steps["data"].level), ("ok", "fail"))
        self.assertEqual(steps["data"].confidence, tp.CONFIDENCE_HIGH)

    def test_blocked_quic_is_a_warning_and_silent_quic_is_unknown(self) -> None:
        blocked = _by_key(_probe(quic=quic_probe.QuicVerdict(quic_probe.QUIC_BLOCKED_BY_NAME, "режут по имени")))["quic"]
        silent = _by_key(_probe(quic=quic_probe.QuicVerdict(quic_probe.QUIC_SILENT, "не отвечает")))["quic"]

        self.assertEqual((blocked.level, silent.level), ("warn", "unknown"))


class ReportAndCardTests(unittest.TestCase):
    def test_target_report_carries_the_path_only_when_checked(self) -> None:
        full = report_text.target_report(_probe())
        bare = report_text.target_report(engine._Probe(target=engine.Target("x.com", "сайт"), service="x", host="x.com"))

        self.assertEqual([step["key"] for step in full["path"]], ["dns", "tcp", "tls", "quic", "data"])
        self.assertIsNone(bare["path"])

    def test_text_report_has_the_path_section(self) -> None:
        lines = report_text.probe_lines(_probe(), full=True)

        self.assertIn("Путь запроса", lines)
        after = lines[lines.index("Путь запроса") + 1 :]
        self.assertTrue(after[0].startswith("   ✅ DNS:"))
        self.assertTrue(any(line.startswith("   ✅ Соединение (TCP):") for line in after))

    def test_card_gets_a_path_section_for_the_main_address(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        target = report["services"][0]["targets"][0]
        target["main"] = True
        target["path"] = [step.as_dict() for step in path_report.build(_probe())]
        cards = build_cards(report_with_words(report))
        sections = [section for card in cards for section in getattr(card, "sections", ()) if section.title == "Путь запроса"]

        self.assertEqual(len(sections), 1)
        self.assertEqual([line.name for line in sections[0].lines][0], "DNS")
        self.assertIn("уверенность высокая", sections[0].lines[0].text)

    def test_old_report_has_no_path_section(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cards = build_cards(report_with_words(report))

        self.assertFalse([section for card in cards for section in getattr(card, "sections", ()) if section.title == "Путь запроса"])


if __name__ == "__main__":
    unittest.main()
