"""Движок вердиктов BlockCheck: исход цели и агрегация отчёта.

Регрессия, ради которой написаны тесты: один несуществующий хост
(``rr5---sn-c0q7lnz7.googlevideo.com``) давал вердикт «Полная блокировка» на
сети без единой блокировки.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.models import (  # noqa: E402
    DPIClassification,
    InconclusiveReason,
    NetworkBaseline,
    SingleTestResult,
    TargetOutcome,
    TargetResult,
    TestStatus,
    TestType,
    VerdictCode,
)
from blockcheck.verdict.aggregate import build_report_verdict  # noqa: E402
from blockcheck.verdict.signatures import judge_target  # noqa: E402


def healthy_baseline(**overrides) -> NetworkBaseline:
    values = {
        "probed": True,
        "internet_ok": True,
        "tls_ok": True,
        "icmp_usable": True,
        "ipv6_usable": True,
        "http80_usable": True,
        "doh_usable": True,
    }
    values.update(overrides)
    return NetworkBaseline(**values)


def probe(test_type: TestType, status: TestStatus, error_code: str | None = None) -> SingleTestResult:
    return SingleTestResult(
        target_name="t", test_type=test_type, status=status, error_code=error_code,
    )


def target(name: str, tests: list[SingleTestResult], **kwargs) -> TargetResult:
    return TargetResult(name=name, value=f"https://{name}", tests=tests, **kwargs)


def web_probes(status: TestStatus, error_code: str | None = None) -> list[SingleTestResult]:
    return [
        probe(TestType.HTTP, status, error_code),
        probe(TestType.TLS_12, status, error_code),
        probe(TestType.TLS_13, status, error_code),
    ]


def blocked(name: str, classification: DPIClassification) -> TargetResult:
    result = target(name, web_probes(TestStatus.FAIL, "TLS_RESET"))
    result.outcome = TargetOutcome.BLOCKED
    result.classification = classification
    return result


def working(name: str) -> TargetResult:
    result = target(name, web_probes(TestStatus.OK))
    result.outcome = TargetOutcome.OK
    return result


def unchecked(name: str) -> TargetResult:
    result = target(name, [])
    result.outcome = TargetOutcome.INCONCLUSIVE
    result.inconclusive_reason = InconclusiveReason.HOST_NOT_RESOLVED
    return result


class JudgeTargetTests(unittest.TestCase):
    def test_unresolved_host_is_inconclusive_not_blocked(self) -> None:
        """Несуществующий хост — отсутствие данных, а не блокировка."""
        dead = target("rr5---sn-c0q7lnz7.googlevideo.com", [
            probe(TestType.HTTP, TestStatus.ERROR, "DNS_ERR"),
        ])

        judgement = judge_target(dead, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.INCONCLUSIVE)
        self.assertEqual(judgement.reason, InconclusiveReason.HOST_NOT_RESOLVED)
        self.assertEqual(judgement.classification, DPIClassification.NONE)

    def test_every_web_probe_failing_on_dns_stays_inconclusive(self) -> None:
        dead = target("dead.example", web_probes(TestStatus.ERROR, "DNS_ERR"))

        judgement = judge_target(dead, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.INCONCLUSIVE)
        self.assertEqual(judgement.reason, InconclusiveReason.HOST_NOT_RESOLVED)

    def test_no_internet_blocks_every_conclusion(self) -> None:
        down = target("discord.com", web_probes(TestStatus.TIMEOUT, "TIMEOUT"))

        judgement = judge_target(down, healthy_baseline(internet_ok=False, tls_ok=False))

        self.assertEqual(judgement.outcome, TargetOutcome.INCONCLUSIVE)
        self.assertEqual(judgement.reason, InconclusiveReason.NO_BASELINE)

    def test_unsupported_only_target_is_inconclusive(self) -> None:
        """Сервер без TLS 1.2 и без IPv6 — не заблокированный сервер."""
        legacy = target("tls13only.example", [
            probe(TestType.TLS_12, TestStatus.UNSUPPORTED, "TLS_UNSUPPORTED"),
            probe(TestType.TLS_13, TestStatus.UNSUPPORTED, "NO_ADDR"),
        ])

        judgement = judge_target(legacy, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.INCONCLUSIVE)
        self.assertEqual(judgement.reason, InconclusiveReason.UNSUPPORTED_ONLY)

    def test_unsupported_probe_does_not_count_as_failure(self) -> None:
        mixed = target("site.example", [
            probe(TestType.HTTP, TestStatus.OK),
            probe(TestType.TLS_12, TestStatus.UNSUPPORTED, "TLS_UNSUPPORTED"),
            probe(TestType.TLS_13, TestStatus.OK),
        ])

        judgement = judge_target(mixed, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.OK)

    def test_tls_reset_is_blocked_with_tls_dpi(self) -> None:
        victim = target("rutracker.org", [
            probe(TestType.HTTP, TestStatus.FAIL, "TLS_RESET"),
            probe(TestType.TLS_12, TestStatus.FAIL, "TLS_RESET"),
            probe(TestType.TLS_13, TestStatus.FAIL, "TLS_RESET"),
        ])

        judgement = judge_target(victim, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.BLOCKED)
        self.assertEqual(judgement.classification, DPIClassification.TLS_DPI)

    def test_https_timeout_with_live_host_is_tls_dpi(self) -> None:
        victim = target("x.com", [
            probe(TestType.HTTP, TestStatus.TIMEOUT, "TIMEOUT"),
            probe(TestType.TLS_12, TestStatus.TIMEOUT, "TIMEOUT"),
            probe(TestType.TLS_13, TestStatus.TIMEOUT, "TIMEOUT"),
            probe(TestType.ISP_PAGE, TestStatus.OK),
        ])

        judgement = judge_target(victim, healthy_baseline())

        self.assertEqual(judgement.classification, DPIClassification.TLS_DPI)

    def test_full_block_requires_working_baseline(self) -> None:
        victim = target("blocked.example", web_probes(TestStatus.TIMEOUT, "TIMEOUT"))

        judgement = judge_target(victim, healthy_baseline())
        self.assertEqual(judgement.classification, DPIClassification.FULL_BLOCK)

        without_baseline = judge_target(victim, NetworkBaseline())
        self.assertEqual(without_baseline.outcome, TargetOutcome.INCONCLUSIVE)

    def test_full_block_needs_enough_probes(self) -> None:
        """Двух упавших проб мало, чтобы объявить полную блокировку."""
        thin = target("thin.example", [
            probe(TestType.HTTP, TestStatus.TIMEOUT, "TIMEOUT"),
            probe(TestType.TLS_12, TestStatus.TIMEOUT, "TIMEOUT"),
        ])

        judgement = judge_target(thin, healthy_baseline())

        self.assertNotEqual(judgement.classification, DPIClassification.FULL_BLOCK)

    def test_isp_page_wins_over_other_signatures(self) -> None:
        victim = target("blocked.example", [
            probe(TestType.HTTP, TestStatus.FAIL, "TLS_RESET"),
            probe(TestType.TLS_12, TestStatus.FAIL, "TLS_RESET"),
            probe(TestType.TLS_13, TestStatus.FAIL, "TLS_RESET"),
            probe(TestType.ISP_PAGE, TestStatus.FAIL, "ISP_PAGE"),
        ])

        judgement = judge_target(victim, healthy_baseline())

        self.assertEqual(judgement.classification, DPIClassification.ISP_PAGE)

    def test_stun_target_is_judged_against_baseline(self) -> None:
        """У STUN-цели нет веб-проб — раньше правило не срабатывало никогда."""
        stun = TargetResult(
            name="Google STUN",
            value="STUN:stun.l.google.com:19302",
            tests=[probe(TestType.STUN, TestStatus.TIMEOUT, "TIMEOUT")],
        )

        judgement = judge_target(stun, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.BLOCKED)
        self.assertEqual(judgement.classification, DPIClassification.STUN_BLOCK)

    def test_ping_failure_alone_never_blocks(self) -> None:
        """CDN штатно молчат на ICMP — это не улика."""
        cdn = target("cdn.discordapp.com", [
            *web_probes(TestStatus.OK),
            probe(TestType.PING, TestStatus.TIMEOUT, "TIMEOUT"),
        ])

        judgement = judge_target(cdn, healthy_baseline())

        self.assertEqual(judgement.outcome, TargetOutcome.OK)
        self.assertEqual(judgement.classification, DPIClassification.NONE)


class ReportVerdictTests(unittest.TestCase):
    def test_single_dead_host_does_not_colour_the_report(self) -> None:
        """Ровно тот случай из отчёта пользователя."""
        targets = [working(f"ok{i}.example") for i in range(6)]
        targets.append(unchecked("rr5---sn-c0q7lnz7.googlevideo.com"))

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.CLEAN)
        self.assertIsNone(verdict.headline)
        self.assertEqual(verdict.valid_targets, 6)
        self.assertEqual(verdict.inconclusive_targets, 1)

    def test_isolated_signature_does_not_reach_the_headline(self) -> None:
        targets = [working(f"ok{i}.example") for i in range(9)]
        targets.append(blocked("odd.example", DPIClassification.TLS_DPI))

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.CLEAN)
        self.assertEqual(len(verdict.isolated), 1)
        self.assertEqual(verdict.confirmed, [])

    def test_two_targets_with_same_signature_pass_quorum(self) -> None:
        targets = [working(f"ok{i}.example") for i in range(8)]
        targets.append(blocked("a.example", DPIClassification.TLS_DPI))
        targets.append(blocked("b.example", DPIClassification.TLS_DPI))

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.SIGNATURES)
        self.assertEqual(verdict.headline, DPIClassification.TLS_DPI)
        self.assertEqual(len(verdict.confirmed), 1)
        self.assertEqual(verdict.confirmed[0].targets, ["a.example", "b.example"])

    def test_several_blocked_sites_are_partial_not_network_wide(self) -> None:
        """Пять недоступных сайтов из семнадцати — не блокировка всей сети."""
        targets = [working(f"ok{i}.example") for i in range(12)]
        targets.extend(
            blocked(f"dead{i}.example", DPIClassification.FULL_BLOCK) for i in range(5)
        )

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.PARTIAL)
        self.assertIsNone(verdict.headline)
        self.assertEqual(verdict.blocked_targets, 5)

    def test_one_blocked_site_stays_isolated(self) -> None:
        targets = [working(f"ok{i}.example") for i in range(17)]
        targets.append(blocked("dead.example", DPIClassification.FULL_BLOCK))

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.CLEAN)
        self.assertEqual(len(verdict.isolated), 1)

    def test_single_checked_target_can_still_report_full_block(self) -> None:
        """Порог кворума не может быть выше числа проверенных целей."""
        targets = [
            blocked("only.example", DPIClassification.FULL_BLOCK),
            unchecked("dead.example"),
        ]

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.SIGNATURES)
        self.assertEqual(verdict.headline, DPIClassification.FULL_BLOCK)

    def test_real_full_block_is_reported(self) -> None:
        targets = [
            blocked(f"dead{i}.example", DPIClassification.FULL_BLOCK) for i in range(5)
        ]

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.SIGNATURES)
        self.assertEqual(verdict.headline, DPIClassification.FULL_BLOCK)

    def test_no_internet_short_circuits_the_report(self) -> None:
        targets = [blocked(f"x{i}.example", DPIClassification.TLS_DPI) for i in range(5)]

        verdict = build_report_verdict(targets, healthy_baseline(internet_ok=False))

        self.assertEqual(verdict.code, VerdictCode.NO_INTERNET)
        self.assertIsNone(verdict.headline)
        self.assertEqual(verdict.confirmed, [])

    def test_all_targets_inconclusive_is_unreliable(self) -> None:
        targets = [unchecked(f"dead{i}.example") for i in range(4)]

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.code, VerdictCode.UNRELIABLE)

    def test_informational_targets_stay_out_of_the_denominator(self) -> None:
        targets = [working("a.example"), working("b.example")]
        ping_target = TargetResult(
            name="CF DNS", value="PING:1.1.1.1", informational=True,
        )
        ping_target.outcome = TargetOutcome.INCONCLUSIVE
        targets.append(ping_target)

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.valid_targets, 2)
        self.assertEqual(verdict.inconclusive_targets, 0)

    def test_headline_picks_the_heaviest_confirmed_signature(self) -> None:
        targets = [
            blocked("a.example", DPIClassification.DNS_FAKE),
            blocked("b.example", DPIClassification.DNS_FAKE),
            blocked("c.example", DPIClassification.TLS_MITM),
            blocked("d.example", DPIClassification.TLS_MITM),
        ]

        verdict = build_report_verdict(targets, healthy_baseline())

        self.assertEqual(verdict.headline, DPIClassification.TLS_MITM)
        self.assertEqual(len(verdict.confirmed), 2)


class SummaryRenderingTests(unittest.TestCase):
    """Карточка итога только отображает вердикт и ничего не решает сама."""

    def _report(self, targets: list[TargetResult], net: NetworkBaseline):
        from blockcheck.models import BlockcheckReport

        report = BlockcheckReport(targets=targets, baseline=net)
        report.verdict = build_report_verdict(targets, net)
        return report

    def _content(self, report):
        from blockcheck.ui.summary_content import build_dpi_summary_content

        return build_dpi_summary_content(
            report=report, is_dark=True, no_dpi_text="DPI не обнаружен",
        )

    def test_dead_host_does_not_produce_a_scary_badge(self) -> None:
        targets = [working(f"ok{i}.example") for i in range(6)]
        targets.append(unchecked("rr5---sn-c0q7lnz7.googlevideo.com"))

        content = self._content(self._report(targets, healthy_baseline()))

        self.assertEqual(content.badge_label, "Блокировок не обнаружено")
        self.assertNotIn("Полная блокировка", content.detail_text)
        self.assertIn("Без вывода", content.detail_text)

    def test_confirmed_signature_reaches_the_badge_and_advice(self) -> None:
        targets = [working(f"ok{i}.example") for i in range(4)]
        targets.append(blocked("a.example", DPIClassification.TLS_DPI))
        targets.append(blocked("b.example", DPIClassification.TLS_DPI))

        content = self._content(self._report(targets, healthy_baseline()))

        self.assertEqual(content.badge_label, "TLS DPI (RST/EOF)")
        self.assertIn("zapret", content.recommendation_text)

    def test_partial_block_badge_does_not_claim_full_block(self) -> None:
        targets = [working(f"ok{i}.example") for i in range(12)]
        targets.extend(
            blocked(f"dead{i}.example", DPIClassification.FULL_BLOCK) for i in range(5)
        )

        content = self._content(self._report(targets, healthy_baseline()))

        self.assertEqual(content.badge_label, "Заблокирована часть ресурсов")
        self.assertNotIn("попробуйте VPN", content.recommendation_text)
        self.assertIn("zapret", content.recommendation_text)

    def test_no_internet_says_so_instead_of_naming_dpi(self) -> None:
        targets = [blocked(f"x{i}.example", DPIClassification.TLS_DPI) for i in range(4)]

        content = self._content(
            self._report(targets, healthy_baseline(internet_ok=False))
        )

        self.assertIn("Нет связи", content.badge_label)
        self.assertIn("подключение", content.recommendation_text)


if __name__ == "__main__":
    unittest.main()
