from __future__ import annotations

import socket
import threading
import unittest
from unittest.mock import Mock, patch

import requests


class PremiumDirectNetworkTests(unittest.TestCase):
    @staticmethod
    def _ok_response() -> Mock:
        response = Mock()
        response.content = b'{"success": true}'
        response.status_code = 200
        response.json.return_value = {"success": True}
        return response

    def test_service_has_no_own_schedule(self) -> None:
        # Когда идти в сеть, решает donater.status_runtime; сервис выполняет
        # ровно ту проверку, о которой его попросили.
        import inspect

        from donater.service import PremiumService

        self.assertFalse(hasattr(PremiumService, "_automatic_network_due"))
        self.assertNotIn("automatic", inspect.signature(PremiumService.check_status).parameters)
        self.assertNotIn(
            "automatic",
            inspect.signature(PremiumService.check_device_activation).parameters,
        )

    def test_request_does_not_pause_winws2_when_plain_route_works(self) -> None:
        from donater.api import PremiumApiClient

        client = PremiumApiClient(base_url="https://premium.example/api")
        with patch(
            "donater.api.request_with_dns_fallback",
            return_value=self._ok_response(),
        ), patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
        ) as direct, patch(
            "winws_runtime.runtime.direct_network.direct_network_pause_needed",
            return_value=True,
        ):
            result = client.get_status()

        self.assertTrue(result["success"])
        direct.assert_not_called()
        self.assertFalse(client.uses_direct_window)

    def test_plain_route_failure_falls_back_to_direct_window_and_remembers_it(self) -> None:
        from donater.api import PremiumApiClient

        client = PremiumApiClient(base_url="https://premium.example/api")
        sends = Mock(
            side_effect=(
                requests.exceptions.SSLError("desync broke tls"),
                self._ok_response(),
                self._ok_response(),
            )
        )
        with patch("donater.api.request_with_dns_fallback", sends), patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
            side_effect=lambda operation: operation(),
        ) as direct, patch(
            "winws_runtime.runtime.direct_network.direct_network_pause_needed",
            return_value=True,
        ):
            first = client.get_status()
            self.assertTrue(first["success"])
            self.assertEqual(direct.call_count, 1)
            self.assertTrue(client.uses_direct_window)

            # Следующий запрос не тратит время на заведомо неудачную попытку.
            second = client.get_status()

        self.assertTrue(second["success"])
        self.assertEqual(direct.call_count, 2)
        self.assertEqual(sends.call_count, 3)

    def test_plain_route_failure_without_running_winws2_is_not_retried(self) -> None:
        from donater.api import PremiumApiClient

        client = PremiumApiClient(base_url="https://premium.example/api")
        sends = Mock(side_effect=requests.exceptions.ConnectTimeout("offline"))
        with patch("donater.api.request_with_dns_fallback", sends), patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
        ) as direct, patch(
            "winws_runtime.runtime.direct_network.direct_network_pause_needed",
            return_value=False,
        ):
            result = client.get_status()

        self.assertEqual(result["error"]["code"], "connect_timeout")
        direct.assert_not_called()
        sends.assert_called_once()

    def test_useless_direct_window_is_not_repeated_while_server_is_down(self) -> None:
        from donater.api import PremiumApiClient

        client = PremiumApiClient(base_url="https://premium.example/api")
        sends = Mock(side_effect=requests.exceptions.ConnectTimeout("server down"))
        with patch("donater.api.request_with_dns_fallback", sends), patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
            side_effect=lambda operation: operation(),
        ) as direct, patch(
            "winws_runtime.runtime.direct_network.direct_network_pause_needed",
            return_value=True,
        ):
            for _attempt in range(4):
                result = client.get_status()
                self.assertEqual(result["error"]["code"], "connect_timeout")

        # Пауза winws2 не помогла — повторные паузы только рвали бы обход.
        self.assertEqual(direct.call_count, 1)
        self.assertFalse(client.uses_direct_window)

    def test_direct_window_memory_is_dropped_when_direct_route_fails(self) -> None:
        from donater.api import PremiumApiClient

        client = PremiumApiClient(base_url="https://premium.example/api")
        sends = Mock(
            side_effect=(
                requests.exceptions.SSLError("desync broke tls"),
                self._ok_response(),
                requests.exceptions.ConnectTimeout("server down"),
                requests.exceptions.ConnectTimeout("server down"),
            )
        )
        with patch("donater.api.request_with_dns_fallback", sends), patch(
            "winws_runtime.runtime.direct_network.run_with_direct_network_access",
            side_effect=lambda operation: operation(),
        ) as direct, patch(
            "winws_runtime.runtime.direct_network.direct_network_pause_needed",
            return_value=True,
        ):
            self.assertTrue(client.get_status()["success"])
            self.assertTrue(client.uses_direct_window)
            self.assertEqual(client.get_status()["error"]["code"], "connect_timeout")
            self.assertFalse(client.uses_direct_window)
            self.assertEqual(client.get_status()["error"]["code"], "connect_timeout")

        self.assertEqual(direct.call_count, 2)

    def test_direct_pause_is_needed_only_for_running_winws2(self) -> None:
        from winws_runtime.runtime.direct_network import direct_network_pause_needed

        running = Mock()
        running.is_running.return_value = True
        stopped = Mock()
        stopped.is_running.return_value = False
        winws1 = Mock(spec=["is_running"])
        winws1.is_running.return_value = True

        for runner, expected in ((running, True), (stopped, False), (winws1, False), (None, False)):
            with self.subTest(expected=expected), patch(
                "winws_runtime.runners.runner_factory.get_current_runner",
                return_value=runner,
            ):
                self.assertEqual(direct_network_pause_needed(), expected)

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
            "donater.api.request_with_dns_fallback",
            side_effect=requests.exceptions.SSLError("desync broke tls"),
        ), patch(
            "winws_runtime.runtime.direct_network.direct_network_pause_needed",
            return_value=True,
        ), patch(
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
                ), patch(
                    "utils.https_dns_fallback.resolve_hostname_via_doh",
                    return_value=(),
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
        ), patch(
            "utils.https_dns_fallback.resolve_hostname_via_doh",
            return_value=(),
        ):
            result = client.get_status()
        self.assertEqual(result["error"]["code"], "dns_error")

    def test_premium_uses_shared_dynamic_dns_transport(self) -> None:
        from donater.api import PremiumApiClient

        response = Mock()
        response.content = b'{"success": true}'
        response.status_code = 200
        response.json.return_value = {"success": True}
        client = PremiumApiClient(base_url="https://premium.example/api")

        with patch(
            "donater.api.request_with_dns_fallback",
            return_value=response,
        ) as request:
            result = client.get_status()

        self.assertTrue(result["success"])
        request.assert_called_once_with(
            client._session,
            "GET",
            "https://premium.example/api/status",
            json=None,
            timeout=client.health_timeout,
        )


if __name__ == "__main__":
    unittest.main()
