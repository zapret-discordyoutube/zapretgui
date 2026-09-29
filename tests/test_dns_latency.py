from __future__ import annotations

import struct
import unittest
from unittest.mock import patch

from dns import latency
from dns.page_workers import DnsLatencyWorker


class DnsLatencyTests(unittest.TestCase):
    def test_query_is_recursive_a_record_request(self) -> None:
        packet = latency.build_dns_query(0x1234, "google.com")

        self.assertEqual(struct.unpack("!HHHHHH", packet[:12]), (0x1234, 0x0100, 1, 0, 0, 0))
        self.assertEqual(packet[12:], b"\x06google\x03com\x00\x00\x01\x00\x01")

    def test_only_matching_response_counts_as_answer(self) -> None:
        answer = struct.pack("!HH", 0x1234, 0x8180) + b"\x00" * 8

        self.assertTrue(latency.is_dns_answer(answer, 0x1234))
        self.assertFalse(latency.is_dns_answer(answer, 0x4321))
        self.assertFalse(latency.is_dns_answer(struct.pack("!HH", 0x1234, 0x0100) + b"\x00" * 8, 0x1234))
        self.assertFalse(latency.is_dns_answer(b"\x12", 0x1234))

    def test_bad_address_is_not_measured(self) -> None:
        self.assertIsNone(latency.measure_server_ms("not-an-ip"))

    def test_report_marks_interception_when_canary_answers(self) -> None:
        answers = {"1.1.1.1": 12.0, "8.8.8.8": None, latency.CANARY_SERVER: 48.0}

        with patch.object(latency, "measure_server_ms", side_effect=lambda server: answers[server]):
            report = latency.measure_dns_latency(["1.1.1.1", "8.8.8.8", "1.1.1.1", ""])

        self.assertEqual(report.results, {"1.1.1.1": 12.0, "8.8.8.8": None})
        self.assertTrue(report.intercepted)

    def test_report_without_interception(self) -> None:
        with patch.object(latency, "measure_server_ms", side_effect=lambda server: None if server == latency.CANARY_SERVER else 5.0):
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
