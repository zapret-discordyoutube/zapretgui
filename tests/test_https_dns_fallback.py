from __future__ import annotations

from pathlib import Path
import socket
import unittest
from unittest.mock import Mock, patch

import requests


class _FallbackSession:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.adapters = {"https://": object()}
        self.routes: list[tuple[str, str]] = []

    def mount(self, prefix, adapter) -> None:
        self.adapters[prefix] = adapter

    def request(self, _method, url, **_kwargs):
        prefix = f"https://{url.split('/', 3)[2]}/"
        adapter = self.adapters.get(prefix)
        if adapter is not None and hasattr(adapter, "resolved_ip"):
            self.routes.append((adapter.hostname, adapter.resolved_ip))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class HttpsDnsFallbackTests(unittest.TestCase):
    def test_fallback_uses_dynamic_addresses_and_keeps_domain_identity(self) -> None:
        from utils.https_dns_fallback import request_with_dns_fallback

        dns_error = requests.ConnectionError("dns")
        dns_error.__cause__ = socket.gaierror(-2, "name resolution")
        response = Mock(spec=requests.Response)
        session = _FallbackSession(
            [
                dns_error,
                requests.exceptions.ConnectTimeout("first address"),
                response,
            ]
        )

        with patch(
            "utils.https_dns_fallback.resolve_hostname_via_doh",
            return_value=("8.8.8.8", "9.9.9.9"),
        ):
            result = request_with_dns_fallback(
                session,
                "GET",
                "https://service.example/api/status",
                timeout=5,
            )

        self.assertIs(result, response)
        self.assertEqual(
            session.routes,
            [
                ("service.example", "8.8.8.8"),
                ("service.example", "9.9.9.9"),
            ],
        )

    def test_read_timeout_is_not_retried(self) -> None:
        from utils.https_dns_fallback import request_with_dns_fallback

        session = _FallbackSession([requests.exceptions.ReadTimeout("sent")])
        with patch(
            "utils.https_dns_fallback.resolve_hostname_via_doh"
        ) as resolver:
            with self.assertRaises(requests.exceptions.ReadTimeout):
                request_with_dns_fallback(
                    session,
                    "POST",
                    "https://service.example/api/mutate",
                    timeout=5,
                    json={"value": 1},
                )
        resolver.assert_not_called()

    def test_stream_keeps_fallback_adapter_until_response_is_closed(self) -> None:
        from utils.https_dns_fallback import (
            ResolvedHttpsAdapter,
            request_with_dns_fallback,
        )

        dns_error = requests.ConnectionError("dns")
        dns_error.__cause__ = socket.gaierror(-2, "name resolution")
        response = Mock(spec=requests.Response)
        session = _FallbackSession([dns_error, response])

        with patch(
            "utils.https_dns_fallback.resolve_hostname_via_doh",
            return_value=("8.8.8.8",),
        ), patch.object(ResolvedHttpsAdapter, "close") as close_adapter:
            result = request_with_dns_fallback(
                session,
                "GET",
                "https://service.example/file.exe",
                timeout=5,
                stream=True,
            )
            close_adapter.assert_not_called()
            result.close()
            close_adapter.assert_called_once_with()

    def test_adapter_connects_to_ip_but_verifies_original_hostname(self) -> None:
        from utils.https_dns_fallback import ResolvedHttpsAdapter

        adapter = ResolvedHttpsAdapter(
            hostname="service.example",
            resolved_ip="8.8.8.8",
        )
        adapter.poolmanager = Mock()
        adapter.build_connection_pool_key_attributes = Mock(
            return_value=(
                {"scheme": "https", "host": "service.example", "port": 443},
                {"ssl_context": object()},
            )
        )
        request = requests.Request("GET", "https://service.example/api").prepare()

        adapter.get_connection_with_tls_context(request, True)
        kwargs = adapter.poolmanager.connection_from_host.call_args.kwargs

        self.assertEqual(kwargs["host"], "8.8.8.8")
        self.assertEqual(kwargs["pool_kwargs"]["server_hostname"], "service.example")
        self.assertEqual(kwargs["pool_kwargs"]["assert_hostname"], "service.example")
        adapter.add_headers(request)
        self.assertEqual(request.headers["Host"], "service.example")

    def test_transport_does_not_embed_current_service_ip(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "utils"
            / "https_dns_fallback.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("144.31.107.88", source)


if __name__ == "__main__":
    unittest.main()
