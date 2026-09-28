from __future__ import annotations

import unittest

from blockcheck.scan_models import StrategyScanReport
import blockcheck.strategy_scan_page_plans as page_plans


class StrategyScanFatalErrorPlanTests(unittest.TestCase):
    def _finalize(self, report: StrategyScanReport, rows=()):
        return page_plans.finalize_scan_report(report, scan_protocol="tcp_https", result_rows=list(rows))

    def test_fatal_error_reaches_finish_plan(self) -> None:
        report = StrategyScanReport(
            target="discord.com",
            total_tested=3,
            total_available=10,
            cancelled=True,
            fatal_error="WinDivert не готов: служба WinDivert отключена в системе (код 1058)",
        )

        plan = self._finalize(report)

        self.assertEqual(plan.fatal_error, report.fatal_error)
        self.assertEqual(plan.support_status_code, "ready_after_error")
        self.assertIn("Остановлено", plan.status_text)
        self.assertEqual(plan.notification_kind, "none")

    def test_plain_cancel_keeps_empty_fatal_error(self) -> None:
        report = StrategyScanReport(
            target="discord.com",
            total_tested=3,
            total_available=10,
            cancelled=True,
        )

        plan = self._finalize(report)

        self.assertEqual(plan.fatal_error, "")
        self.assertEqual(plan.support_status_code, "ready")
        self.assertIn("Отменено", plan.status_text)

    def test_declined_open_target_warns_instead_of_not_found(self) -> None:
        report = StrategyScanReport(target="discord.com", total_tested=0, baseline_accessible=True)

        plan = self._finalize(report)

        self.assertEqual(plan.notification_kind, "baseline_accessible")

    def test_only_reliable_strategies_count_as_working(self) -> None:
        report = StrategyScanReport(target="discord.com", total_tested=2)

        plan = self._finalize(report, rows=[{"success": True}, {"success": False, "verdict": "unstable"}])

        self.assertEqual(plan.working_count, 1)
        self.assertEqual(plan.notification_kind, "found")


class StrategyScanResultPresentationTests(unittest.TestCase):
    def _result(self, **fields):
        from blockcheck.scan_models import StrategyProbeResult

        base = dict(
            strategy_name="fake",
            strategy_id="fake",
            strategy_args="--lua-desync=fake",
            target="discord.com",
            success=False,
            time_ms=0.0,
        )
        base.update(fields)
        return StrategyProbeResult(**base)

    def test_working_shows_attempts_and_apply(self) -> None:
        row = page_plans.build_result_presentation(
            self._result(
                success=True,
                verdict="working",
                attempts_ok=3,
                attempts_total=3,
                time_ms=120.0,
                apply_lines=("--filter-tcp=443",),
            ),
            row_number=4,
        )
        self.assertEqual((row.number_text, row.status_text, row.time_text), ("4", "Работает 3/3", "120"))
        self.assertTrue(row.can_apply)

    def test_unstable_and_crash_cannot_be_applied(self) -> None:
        unstable = page_plans.build_result_presentation(
            self._result(verdict="unstable", attempts_ok=1, attempts_total=3, error="нет ответа (таймаут)"),
            row_number=1,
        )
        crash = page_plans.build_result_presentation(self._result(verdict="crash", error="lua error"), row_number=2)
        self.assertEqual(unstable.status_text, "Нестабильно 1/3")
        self.assertFalse(unstable.can_apply)
        self.assertEqual(crash.status_text, "Сбой winws2")
        self.assertFalse(crash.can_apply)


if __name__ == "__main__":
    unittest.main()
