from __future__ import annotations

import socket
import threading
import unittest
from unittest.mock import Mock, patch

import requests


class PremiumDirectNetworkTests(unittest.TestCase):
    def test_background_network_runs_on_start_then_every_three_hours(self) -> None:
        from donater.service import PremiumService

        service = PremiumService(api_base_url="https://premium.example/api")
        with patch("donater.service.time.monotonic", side_effect=(100.0, 101.0, 10901.0)):
            self.assertTrue(service._automatic_network_due(has_pending_pairing=False))
            self.assertFalse(service._automatic_network_due(has_pending_pairing=False))
            self.assertTrue(service._automatic_network_due(has_pending_pairing=False))

        service._last_background_network_attempt_at = 200.0
        with patch("donater.service.time.monotonic", return_value=201.0):
            self.assertTrue(service._automatic_network_due(has_pending_pairing=True))
        self.assertEqual(service._last_background_network_attempt_at, 200.0)

    def test_direct_boundary_uses_active_winws2_owner(self) -> None:
        from winws_runtime.runtime.direct_network import run_with_direct_network_access

        runner = Mock()
        runner.run_with_direct_network_access.side_effect = lambda operation: operation()
        operation = Mock(return_value="ok")

        with patch(
            "winws_runtime.runners.runner_factory.get_current_runner",
            return_value=runner,
        ):
            self.assertEqual(run_with_direct_network_access(operation), "ok")

        runner.run_with_direct_network_access.assert_called_once()
        operation.assert_called_once_with()

    def test_winws2_owner_restores_exact_preset_after_request(self) -> None:
        from winws_runtime.runners.zapret2_runner import Winws2StrategyRunner

        runner = object.__new__(Winws2StrategyRunner)
        runner._operation_lock = threading.RLock()
        runner.running_process = object()
        runner._preset_file_path = __file__
        runner.current_launch_label = "Точный preset"
        runner.is_running = Mock(return_value=True)
        runner._stop_process_only_locked = Mock(return_value=True)
        runner._start_from_preset_file_locked = Mock(return_value=True)
        operation = Mock(return_value="response")

        self.assertEqual(runner.run_with_direct_network_access(operation), "response")

        runner._stop_process_only_locked.assert_called_once_with()
        runner._start_from_preset_file_locked.assert_called_once_with(
            __file__,
            "Точный preset",
            force_cleanup=False,
            retry_count=0,
            stable_start_window_seconds=0.3,
        )

    def test_restore_failure_is_returned_as_typed_premium_error(self) -> None:
        from donater.api import PremiumApiClient
        from winws_runtime.runtime.direct_network import DirectNetworkAccessError

        client = PremiumApiClient(base_url="https://premium.example/api")
        client._session = Mock()
        with patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
            side_effect=DirectNetworkAccessError("restore failed"),
        ):
            result = client.get_status()

        self.assertEqual(result["error"]["code"], "winws_restore_failed")
        self.assertFalse(result["error"]["retryable"])

    def test_api_classifies_tls_dns_and_timeout_errors(self) -> None:
        from donater.api import PremiumApiClient

        cases = (
            (requests.exceptions.SSLError("certificate"), "tls_error"),
            (requests.exceptions.ConnectTimeout("connect"), "connect_timeout"),
            (requests.exceptions.ReadTimeout("read"), "read_timeout"),
        )
        for error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                client = PremiumApiClient(base_url="https://premium.example/api")
                client._session = Mock()
                client._session.request.side_effect = error
                with patch(
                    "winws_runtime.runtime.direct_network.run_with_direct_network_access",
                    side_effect=lambda operation: operation(),
                ):
                    result = client.get_status()
                self.assertEqual(result["error"]["code"], expected_code)

        dns_error = requests.exceptions.ConnectionError("dns")
        dns_error.__cause__ = socket.gaierror(-2, "name resolution")
        client = PremiumApiClient(base_url="https://premium.example/api")
        client._session = Mock()
        client._session.request.side_effect = dns_error
        with patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
            side_effect=lambda operation: operation(),
        ):
            result = client.get_status()
        self.assertEqual(result["error"]["code"], "dns_error")


if __name__ == "__main__":
    unittest.main()
