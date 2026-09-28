"""Авто-замена голого googlevideo.com на rr-хост при старте подбора стратегии."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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
) -> StrategyScanWorker:
    return StrategyScanWorker(
        target=target,
        mode="quick",
        scan_protocol=scan_protocol,
        shutdown_sync=lambda *a, **k: None,
        start_run_log=lambda **k: SimpleNamespace(path=None),
        append_run_log=lambda *_a: None,
        close_run_log=lambda *_a: None,
        environment_factory=lambda **_kw: object(),
    )


_SEARCH = "blockcheck.strategy_search.engine.run_strategy_search"


def _request(search_mock):
    return search_mock.call_args.args[0]


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

        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host",
            return_value=GoogleVideoDiscoveryResult(
                host="rr5---sn-test.googlevideo.com",
                detail="найдено вариантов: 1",
            ),
        ) as discover, patch(
            _SEARCH,
            return_value=_stub_scanner_report("googlevideo.com"),
        ) as search:
            worker.run()

        discover.assert_called_once()
        # Проверяется видеосервер, а профиль пишется на googlevideo.com.
        self.assertEqual(_request(search).probe_host, "rr5---sn-test.googlevideo.com")
        self.assertEqual(_request(search).target, "googlevideo.com")
        self.assertTrue(
            any("rr5---sn-test.googlevideo.com" in line for line in logs),
            logs,
        )

    def test_bare_googlevideo_discovery_failure_aborts_with_fatal_error(self) -> None:
        worker = _make_worker("googlevideo.com")
        finished: list[object] = []
        worker.scan_finished.connect(finished.append)

        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host",
            return_value=GoogleVideoDiscoveryResult(detail="YouTube не отдал адресов"),
        ), patch(_SEARCH) as search:
            worker.run()

        search.assert_not_called()
        self.assertEqual(len(finished), 1)
        report = finished[0]
        self.assertTrue(report.cancelled)
        self.assertIn("YouTube не отдал адресов", report.fatal_error)
        self.assertEqual(report.target, "googlevideo.com")
        self.assertEqual(report.total_tested, 0)

    def test_non_googlevideo_target_skips_discovery(self) -> None:
        worker = _make_worker("discord.com")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host"
        ) as discover, patch(_SEARCH, return_value=_stub_scanner_report("discord.com")) as search:
            worker.run()

        discover.assert_not_called()
        self.assertEqual(_request(search).target, "discord.com")
        self.assertEqual(_request(search).probe_host, "")

    def test_rr_host_target_not_touched(self) -> None:
        worker = _make_worker("rr5---sn-abc.googlevideo.com")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host"
        ) as discover, patch(_SEARCH, return_value=_stub_scanner_report("rr5---sn-abc.googlevideo.com")) as search:
            worker.run()

        discover.assert_not_called()
        self.assertEqual(_request(search).target, "rr5---sn-abc.googlevideo.com")

    def test_stun_protocol_skips_discovery(self) -> None:
        worker = _make_worker("stun.l.google.com:19302", scan_protocol="stun_voice")
        with patch(
            "blockcheck.googlevideo_discovery.discover_googlevideo_host"
        ) as discover, patch(_SEARCH, return_value=_stub_scanner_report("stun.l.google.com:19302")):
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
        ), patch(_SEARCH) as search:
            worker.run()

        search.assert_not_called()
        self.assertEqual(len(finished), 1)
        report = finished[0]
        self.assertTrue(report.cancelled)
        self.assertEqual(report.fatal_error, "")


class StrategyScanWorkerQuestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_question_waits_for_answer_from_window(self) -> None:
        import threading

        worker = _make_worker("discord.com")
        worker.continue_question.connect(lambda _reason: None)
        answers = []
        thread = threading.Thread(target=lambda: answers.append(worker.ask_continue("открыто без обхода")))
        thread.start()
        worker.answer_continue(True)
        thread.join(2)
        self.assertEqual(answers, [True])

    def test_stop_unblocks_pending_question_with_no(self) -> None:
        import threading

        worker = _make_worker("discord.com")
        answers = []
        thread = threading.Thread(target=lambda: answers.append(worker.ask_continue("открыто без обхода")))
        thread.start()
        worker.stop()
        thread.join(2)
        self.assertEqual(answers, [False])

    def test_runtime_is_restored_only_when_it_was_running(self) -> None:
        calls = []
        worker = _make_worker("discord.com")
        worker.set_runtime_restore(was_running=False, restore=lambda: calls.append("start"))
        self.assertFalse(worker.restore_runtime_if_needed())

        worker = _make_worker("discord.com")
        worker.set_runtime_restore(was_running=True, restore=lambda: calls.append("start"))
        self.assertTrue(worker.restore_runtime_if_needed())
        # Второй раз не запускает.
        self.assertFalse(worker.restore_runtime_if_needed())
        self.assertEqual(calls, ["start"])

    def test_manual_start_during_scan_cancels_restore(self) -> None:
        from blockcheck.ui.strategy_scan_page import StrategyScanPage

        calls = []
        worker = _make_worker("discord.com")
        worker.set_runtime_restore(was_running=True, restore=lambda: calls.append("start"))
        page = StrategyScanPage.__new__(StrategyScanPage)
        page._scan_worker = worker
        page._strategy_scan_run_runtime = SimpleNamespace(is_running=lambda: True)
        page._on_stop = lambda: None

        self.assertTrue(StrategyScanPage.request_runtime_conflicting_stop(page))
        self.assertFalse(worker.restore_runtime_if_needed())
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
