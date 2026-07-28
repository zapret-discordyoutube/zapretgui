"""Бюджет HTTPS-пробы: хост с несколькими адресами не стоит N таймаутов.

Регрессия: ``test_https`` перебирал все A/AAAA-записи подряд, каждую с полным
таймаутом. Хост с четырьмя адресами и блокировкой стоил 40 секунд на одну
пробу, а таких проб на цель приходится три.
"""

from __future__ import annotations

import socket
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck import tls_tester  # noqa: E402
from blockcheck.config import HTTPS_CONNECT_TIMEOUT, HTTPS_MAX_ADDRESSES  # noqa: E402
from blockcheck.models import TestStatus  # noqa: E402


FIVE_ADDRESSES = [
    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (f"10.0.0.{i}", 443))
    for i in range(1, 6)
]


class _Attempts:
    def __init__(self, sleep_factor: float = 0.0):
        self.timeouts: list[float] = []
        self.sleep_factor = sleep_factor


class _FakeSocket:
    def __init__(self, attempts: _Attempts):
        self._attempts = attempts
        self.timeout: float | None = None

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        pass


class _FakeSSLSocket(_FakeSocket):
    """Соединение, которое всегда упирается в таймаут."""

    def connect(self, _address):
        self._attempts.timeouts.append(float(self.timeout or 0.0))
        if self._attempts.sleep_factor:
            time.sleep(float(self.timeout or 0.0) * self._attempts.sleep_factor)
        raise socket.timeout("timed out")


class _FakeContext:
    minimum_version = None
    maximum_version = None

    def __init__(self, attempts: _Attempts):
        self._attempts = attempts

    def wrap_socket(self, sock, server_hostname=None):
        ssl_sock = _FakeSSLSocket(self._attempts)
        ssl_sock.timeout = sock.timeout
        return ssl_sock


class ProbeBudgetTests(unittest.TestCase):
    def _run(
        self,
        *,
        timeout: int,
        sleep_factor: float = 0.0,
        connect_timeout: float | None = None,
    ) -> tuple[_Attempts, object]:
        attempts = _Attempts(sleep_factor=sleep_factor)
        patches = [
            patch("blockcheck.tls_tester.resolve_addrinfo", return_value=FIVE_ADDRESSES),
            patch(
                "blockcheck.tls_tester.socket.socket",
                side_effect=lambda *a, **k: _FakeSocket(attempts),
            ),
            patch(
                "blockcheck.tls_tester.ssl.create_default_context",
                side_effect=lambda: _FakeContext(attempts),
            ),
        ]
        if connect_timeout is not None:
            patches.append(
                patch("blockcheck.tls_tester.HTTPS_CONNECT_TIMEOUT", connect_timeout)
            )

        for item in patches:
            item.start()
        try:
            result = tls_tester.test_https(
                "blocked.example", timeout=timeout, ip_family="ipv4",
            )
        finally:
            for item in reversed(patches):
                item.stop()

        return attempts, result

    def test_only_a_couple_of_addresses_are_tried(self) -> None:
        attempts, result = self._run(timeout=30)

        self.assertLessEqual(len(attempts.timeouts), HTTPS_MAX_ADDRESSES)
        self.assertEqual(result.status, TestStatus.TIMEOUT)

    def test_single_attempt_never_waits_longer_than_the_connect_timeout(self) -> None:
        attempts, _ = self._run(timeout=30)

        for value in attempts.timeouts:
            self.assertLessEqual(value, HTTPS_CONNECT_TIMEOUT)

    def test_total_wait_stays_within_the_call_budget(self) -> None:
        """Бюджет делится между адресами, а не выдаётся каждому целиком."""
        budget = 2
        attempts, _ = self._run(timeout=budget, sleep_factor=1.0, connect_timeout=0.5)

        self.assertEqual(len(attempts.timeouts), HTTPS_MAX_ADDRESSES)
        self.assertLessEqual(sum(attempts.timeouts), budget)

    def test_wall_clock_respects_the_budget(self) -> None:
        started = time.monotonic()
        attempts, _ = self._run(timeout=2, sleep_factor=1.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3.0)
        self.assertLessEqual(len(attempts.timeouts), HTTPS_MAX_ADDRESSES)

    def test_exhausted_budget_stops_further_attempts(self) -> None:
        attempts, _ = self._run(timeout=1, sleep_factor=1.0)

        self.assertEqual(len(attempts.timeouts), 1)


if __name__ == "__main__":
    unittest.main()
