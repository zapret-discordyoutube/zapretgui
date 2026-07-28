"""Планировщик проб BlockCheck: резолв, дедлайн, отмена, экономия проб.

Сеть не трогается: все пробы подменены. Проверяется именно расписание —
сколько раз резолвится хост, какие пробы вообще ставятся в очередь и что
происходит при отмене и превышении лимита времени.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.models import (  # noqa: E402
    InconclusiveReason,
    NetworkBaseline,
    SingleTestResult,
    TargetOutcome,
    TestStatus,
    TestType,
    VerdictCode,
)
from blockcheck.runner import BlockcheckRunner, RunMode  # noqa: E402


TARGETS = [
    {"name": "Alive", "value": "https://alive.example"},
    {"name": "Dead", "value": "https://dead.example"},
    {"name": "Google STUN", "value": "STUN:stun.example:3478"},
    {"name": "CF DNS", "value": "PING:1.1.1.1"},
]

RESOLUTION = {
    "alive.example": (["1.2.3.4"], ["2001:db8::1"]),
    "dead.example": ([], []),
}


def baseline(**overrides) -> NetworkBaseline:
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


def ok_result(test_type: TestType) -> SingleTestResult:
    return SingleTestResult(
        target_name="t", test_type=test_type, status=TestStatus.OK, detail="ok",
    )


class _Harness:
    """Подменяет все сетевые вызовы runner'а и считает обращения."""

    def __init__(self, *, net_baseline: NetworkBaseline | None = None, slow: float = 0.0):
        self.baseline = net_baseline or baseline()
        self.slow = slow
        self.resolve_calls: Counter[str] = Counter()
        self.probe_calls: Counter[str] = Counter()
        self._patches: list = []

    def _resolve_ips(self, host, **_kwargs):
        self.resolve_calls[host] += 1
        return RESOLUTION.get(host, ([], []))

    def _count(self, name: str):
        self.probe_calls[name] += 1
        if self.slow:
            time.sleep(self.slow)

    def __enter__(self):
        def _https(host, timeout=None, tls_version=None, ip_family="auto", **_kw):
            self._count(f"https:{host}:{ip_family}")
            return ok_result(
                {None: TestType.HTTP, "1.2": TestType.TLS_12, "1.3": TestType.TLS_13}[tls_version]
            )

        def _isp(host, timeout=None, resolved_ip=None, **_kw):
            self._count(f"isp:{host}")
            return ok_result(TestType.ISP_PAGE)

        def _ping(host, count=None, timeout=None, resolved_ip=None, **_kw):
            self._count(f"ping:{host}")
            return ok_result(TestType.PING)

        def _stun(host, port=None, timeout=None, **_kw):
            self._count(f"stun:{host}")
            return ok_result(TestType.STUN)

        def _tcp(url, **_kw):
            self._count("tcp")
            return ok_result(TestType.TCP_16_20)

        targets = [
            patch("blockcheck.runner.build_targets_with_user_domains", return_value=list(TARGETS)),
            patch("blockcheck.runner.probe_baseline", return_value=self.baseline),
            patch("blockcheck.runner.resolve_ips", side_effect=self._resolve_ips),
            patch("blockcheck.runner.clear_dns_cache"),
            patch("blockcheck.runner.test_https", side_effect=_https),
            patch("blockcheck.runner.check_http_injection", side_effect=_isp),
            patch("blockcheck.runner.detect_isp_page", side_effect=_isp),
            patch("blockcheck.runner.ping_host", side_effect=_ping),
            patch("blockcheck.runner.test_stun", side_effect=_stun),
            patch("blockcheck.runner.check_tcp_16_20", side_effect=_tcp),
            patch("blockcheck.runner.check_dns_integrity", return_value=[]),
            patch("blockcheck.runner.load_tcp_targets_with_source", return_value=([], "test")),
            patch("blockcheck.runner.load_domains_with_source", return_value=([], "test")),
        ]
        for item in targets:
            item.start()
            self._patches.append(item)
        return self

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.stop()
        return False


class RunnerSchedulingTests(unittest.TestCase):
    def test_each_host_is_resolved_exactly_once(self) -> None:
        """Раньше один хост резолвился до пяти раз за прогон."""
        with _Harness() as harness:
            BlockcheckRunner(mode=RunMode.FULL).run()

        self.assertEqual(dict(harness.resolve_calls), {"alive.example": 1, "dead.example": 1})

    def test_unresolved_host_costs_no_probes(self) -> None:
        with _Harness() as harness:
            report = BlockcheckRunner(mode=RunMode.FULL).run()

        self.assertEqual(
            [key for key in harness.probe_calls if "dead.example" in key], [],
        )
        dead = next(item for item in report.targets if item.name == "Dead")
        self.assertEqual(dead.outcome, TargetOutcome.INCONCLUSIVE)
        self.assertEqual(dead.inconclusive_reason, InconclusiveReason.HOST_NOT_RESOLVED)

    def test_dead_host_does_not_spoil_the_report_verdict(self) -> None:
        with _Harness():
            report = BlockcheckRunner(mode=RunMode.FULL).run()

        self.assertEqual(report.verdict.code, VerdictCode.CLEAN)
        self.assertEqual(report.verdict.inconclusive_targets, 1)

    def test_ipv6_probes_are_skipped_without_ipv6_connectivity(self) -> None:
        with _Harness(net_baseline=baseline(ipv6_usable=False)) as harness:
            BlockcheckRunner(mode=RunMode.FULL).run()

        self.assertEqual([key for key in harness.probe_calls if key.endswith("ipv6")], [])
        self.assertEqual(harness.probe_calls["https:alive.example:ipv4"], 3)

    def test_ipv6_probes_run_when_available(self) -> None:
        with _Harness() as harness:
            BlockcheckRunner(mode=RunMode.FULL).run()

        self.assertEqual(harness.probe_calls["https:alive.example:ipv6"], 3)

    def test_ping_probes_are_skipped_without_icmp(self) -> None:
        """ICMP режется на машине — незачем 20 раз получать TIMEOUT."""
        with _Harness(net_baseline=baseline(icmp_usable=False)) as harness:
            report = BlockcheckRunner(mode=RunMode.FULL).run()

        self.assertEqual([key for key in harness.probe_calls if key.startswith("ping:")], [])
        self.assertNotIn("CF DNS", [item.name for item in report.targets])

    def test_ping_target_is_informational(self) -> None:
        with _Harness():
            report = BlockcheckRunner(mode=RunMode.FULL).run()

        ping_target = next(item for item in report.targets if item.name == "CF DNS")
        self.assertTrue(ping_target.informational)

    def test_quick_mode_skips_isp_stun_and_dns(self) -> None:
        with _Harness() as harness:
            BlockcheckRunner(mode=RunMode.QUICK).run()

        self.assertEqual([key for key in harness.probe_calls if key.startswith("isp:")], [])
        self.assertEqual([key for key in harness.probe_calls if key.startswith("stun:")], [])

    def test_deadline_stops_the_run_and_marks_targets(self) -> None:
        with _Harness(slow=0.3):
            runner = BlockcheckRunner(mode=RunMode.FULL, deadline_seconds=0.05)
            started = time.monotonic()
            report = runner.run()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3.0)
        alive = next(item for item in report.targets if item.name == "Alive")
        self.assertEqual(alive.outcome, TargetOutcome.INCONCLUSIVE)
        self.assertEqual(alive.inconclusive_reason, InconclusiveReason.BUDGET_EXCEEDED)
        self.assertEqual(report.verdict.code, VerdictCode.UNRELIABLE)

    def test_cancel_before_resolution_does_not_claim_hosts_are_dead(self) -> None:
        """Отмена — это отсутствие данных, а не «хоста не существует»."""
        with _Harness():
            runner = BlockcheckRunner(mode=RunMode.FULL)
            runner.cancel()
            report = runner.run()

        for item in report.targets:
            self.assertNotEqual(
                item.inconclusive_reason, InconclusiveReason.HOST_NOT_RESOLVED,
            )

    def test_cancel_returns_without_waiting_for_hanging_probes(self) -> None:
        release = threading.Event()

        def _hang(*_args, **_kwargs):
            release.wait(timeout=2.0)
            return ok_result(TestType.HTTP)

        with _Harness():
            runner = BlockcheckRunner(mode=RunMode.FULL)
            with patch("blockcheck.runner.test_https", side_effect=_hang):
                threading.Timer(0.05, runner.cancel).start()
                started = time.monotonic()
                report = runner.run()
                elapsed = time.monotonic() - started
        release.set()

        self.assertTrue(report.cancelled)
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
