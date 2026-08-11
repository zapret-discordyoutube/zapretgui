"""Авто-замена голого googlevideo.com на rr-хост при старте подбора стратегии."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from blockcheck.googlevideo_discovery import GoogleVideoDiscoveryResult  # noqa: E402
from blockcheck.scan_models import StrategyScanReport  # noqa: E402
from blockcheck.strategy_scan_worker import StrategyScanWorker  # noqa: E402


def _make_worker(
    target: str,
    *,
    scan_protocol: str = "tcp_https",
    start_index: int = 0,
) -> StrategyScanWorker:
    return StrategyScanWorker(
        target=target,
        mode="quick",
        start_index=start_index,
        scan_protocol=scan_protocol,
        shutdown_sync=lambda *a, **k: None,
        start_run_log=lambda **k: SimpleNamespace(path=None),
        append_run_log=lambda *_a: None,
        close_run_log=lambda *_a: None,
    )


def _stub_scanner_report(target: str) -> StrategyScanReport:
    return StrategyScanReport(target=target, total_tested=0)


class StrategyScanGoogleVideoTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_bare_googlevideo_replaced_before_scanner_start(self) -> None:
        worker = _make_worker("googlevideo.com")
        logs: list[str] = []
        worker.scan_log.connect(logs.append)

        scanner = Mock()
        scanner.run.return_value = _stub_scanner_report("rr5---sn-test.googlevideo.com")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host",
            return_value=GoogleVideoDiscoveryResult(
                host="rr5---sn-test.googlevideo.com",
                detail="найдено вариантов: 1",
            ),
        ) as discover, patch(
            "blockcheck.strategy_scanner.StrategyScanner",
            return_value=scanner,
        ) as scanner_cls:
            worker.run()

        discover.assert_called_once()
        self.assertEqual(
            scanner_cls.call_args.kwargs["target"],
            "rr5---sn-test.googlevideo.com",
        )
        self.assertTrue(
            any("rr5---sn-test.googlevideo.com" in line for line in logs),
            logs,
        )

    def test_bare_googlevideo_discovery_failure_aborts_with_fatal_error(self) -> None:
        worker = _make_worker("googlevideo.com", start_index=3)
        finished: list[object] = []
        worker.scan_finished.connect(finished.append)

        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host",
            return_value=GoogleVideoDiscoveryResult(detail="YouTube не отдал адресов"),
        ), patch("blockcheck.strategy_scanner.StrategyScanner") as scanner_cls:
            worker.run()

        scanner_cls.assert_not_called()
        self.assertEqual(len(finished), 1)
        report = finished[0]
        self.assertTrue(report.cancelled)
        self.assertIn("YouTube не отдал адресов", report.fatal_error)
        self.assertEqual(report.target, "googlevideo.com")
        self.assertEqual(report.total_tested, 3)

    def test_non_googlevideo_target_skips_discovery(self) -> None:
        worker = _make_worker("discord.com")
        scanner = Mock()
        scanner.run.return_value = _stub_scanner_report("discord.com")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host"
        ) as discover, patch(
            "blockcheck.strategy_scanner.StrategyScanner",
            return_value=scanner,
        ) as scanner_cls:
            worker.run()

        discover.assert_not_called()
        self.assertEqual(scanner_cls.call_args.kwargs["target"], "discord.com")

    def test_rr_host_target_not_touched(self) -> None:
        worker = _make_worker("rr5---sn-abc.googlevideo.com")
        scanner = Mock()
        scanner.run.return_value = _stub_scanner_report("rr5---sn-abc.googlevideo.com")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host"
        ) as discover, patch(
            "blockcheck.strategy_scanner.StrategyScanner",
            return_value=scanner,
        ) as scanner_cls:
            worker.run()

        discover.assert_not_called()
        self.assertEqual(
            scanner_cls.call_args.kwargs["target"],
            "rr5---sn-abc.googlevideo.com",
        )

    def test_stun_protocol_skips_discovery(self) -> None:
        worker = _make_worker("stun.l.google.com:19302", scan_protocol="stun_voice")
        scanner = Mock()
        scanner.run.return_value = _stub_scanner_report("stun.l.google.com:19302")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host"
        ) as discover, patch(
            "blockcheck.strategy_scanner.StrategyScanner",
            return_value=scanner,
        ):
            worker.run()

        discover.assert_not_called()

    def test_cancelled_during_discovery_is_plain_cancel(self) -> None:
        worker = _make_worker("googlevideo.com")
        finished: list[object] = []
        worker.scan_finished.connect(finished.append)

        def cancel_and_fail(**_kwargs) -> GoogleVideoDiscoveryResult:
            worker.stop()
            return GoogleVideoDiscoveryResult(detail="проверка остановлена")

        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host",
            side_effect=cancel_and_fail,
        ), patch("blockcheck.strategy_scanner.StrategyScanner") as scanner_cls:
            worker.run()

        scanner_cls.assert_not_called()
        self.assertEqual(len(finished), 1)
        report = finished[0]
        self.assertTrue(report.cancelled)
        self.assertEqual(report.fatal_error, "")


if __name__ == "__main__":
    unittest.main()
