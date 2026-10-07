from __future__ import annotations

import unittest
from unittest.mock import patch

from dns import latency
from dns.page_workers import DnsLatencyWorker
from utils.dns_wire import FAILURE_TIMEOUT, STATUS_OK, STATUS_TIMEOUT, DnsQueryResult


class DnsLatencyTests(unittest.TestCase):
    def test_bad_address_is_not_measured(self) -> None:
        self.assertIsNone(latency.measure_server_ms("not-an-ip"))

    def test_best_of_several_attempts_is_taken_and_silence_is_none(self) -> None:
        times = iter([30.0, None, 12.0])

        def fake_query(server, _name, _rtype, **_kwargs):
            elapsed = next(times)
            if elapsed is None:
                return DnsQueryResult(status=STATUS_TIMEOUT, failure=FAILURE_TIMEOUT)
            return DnsQueryResult(status=STATUS_OK, rcode=0, elapsed_ms=elapsed)

        with patch.object(latency, "query_udp", fake_query):
            self.assertEqual(latency.measure_server_ms("1.1.1.1", attempts=3), 12.0)

        silent = DnsQueryResult(status=STATUS_TIMEOUT, failure=FAILURE_TIMEOUT)
        with patch.object(latency, "query_udp", lambda *a, **k: silent):
            self.assertIsNone(latency.measure_server_ms("1.1.1.1", attempts=2))

    def test_speed_is_measured_over_udp_only(self) -> None:
        """Усечённый ответ — тоже ответ UDP: переход на TCP исказил бы время."""
        truncated = DnsQueryResult(status=STATUS_OK, rcode=0, elapsed_ms=9.0, truncated=True)

        with patch.object(latency, "query_udp", lambda *a, **k: truncated):
            self.assertEqual(latency.measure_server_ms("1.1.1.1", attempts=1), 9.0)

    def test_report_marks_interception_when_canary_answers(self) -> None:
        answers = {"1.1.1.1": 12.0, "8.8.8.8": None}

        with (
            patch.object(latency, "measure_server_ms", side_effect=lambda server: answers[server]),
            patch.object(latency, "canary_answered", return_value=True),
        ):
            report = latency.measure_dns_latency(["1.1.1.1", "8.8.8.8", "1.1.1.1", ""])

        self.assertEqual(report.results, {"1.1.1.1": 12.0, "8.8.8.8": None})
        self.assertTrue(report.intercepted)

    def test_report_without_interception(self) -> None:
        with (
            patch.object(latency, "measure_server_ms", return_value=5.0),
            patch.object(latency, "canary_answered", return_value=False),
        ):
            report = latency.measure_dns_latency(["9.9.9.9"])

        self.assertEqual(report.results, {"9.9.9.9": 5.0})
        self.assertFalse(report.intercepted)
        self.assertEqual(latency.measure_dns_latency([]), latency.DnsLatencyReport())

    def test_worker_emits_report_or_error(self) -> None:
        worker = DnsLatencyWorker(7, servers=["1.1.1.1", ""], measure_dns_latency=lambda servers: ("ok", servers))
        completed: list = []
        worker.completed.connect(lambda request_id, report: completed.append((request_id, report)))
        worker.run()
        self.assertEqual(completed, [(7, ("ok", ["1.1.1.1"]))])

        def broken(_servers):
            raise OSError("no network")

        failing = DnsLatencyWorker(8, servers=["1.1.1.1"], measure_dns_latency=broken)
        errors: list = []
        failing.failed.connect(lambda request_id, error: errors.append((request_id, error)))
        failing.run()
        self.assertEqual(errors, [(8, "no network")])


if __name__ == "__main__":
    unittest.main()
