"""Регрессии на зависание BlockCheck из-за неограниченного разрешения имён.

История бага: ``socket.getaddrinfo`` не имеет таймаута и не прерывается.
Preflight «защищался» через ``future.result(timeout=3)`` + ``future.cancel()``,
но cancel() не действует на запущенную задачу, поток пула оставался занят
навсегда, а ``pool.shutdown(wait=True)`` в finally его дожидался. На мёртвом
DNS проверка одного домена занимала не 3 секунды, а столько, сколько система
отводит на резолв; «Остановить» не работал, а незавершённые non-daemon потоки
пула не давали процессу закрыться.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))


class _BlackholeDNS:
    """Подменяет getaddrinfo на вечно висящий вызов — как мёртвый DNS-сервер."""

    def __init__(self, delay: float = 600.0) -> None:
        self._delay = delay
        self._original = None
        self.released = threading.Event()

    def __enter__(self):
        import utils.net_resolve as net_resolve

        self._original = socket.getaddrinfo

        def _hang(*_args, **_kwargs):
            # Не time.sleep: тест не должен ждать реального завершения потока.
            self.released.wait(self._delay)
            raise socket.gaierror(socket.EAI_NONAME, "blackhole")

        socket.getaddrinfo = _hang
        net_resolve.clear_cache()
        return self

    def __exit__(self, *_exc):
        socket.getaddrinfo = self._original
        self.released.set()

        import utils.net_resolve as net_resolve

        net_resolve.clear_cache()
        return False


class NetResolveDeadlineTests(unittest.TestCase):
    def test_resolve_respects_deadline_on_dead_dns(self) -> None:
        from utils.net_resolve import DNSTimeoutError, resolve_addrinfo

        with _BlackholeDNS():
            started = time.monotonic()
            with self.assertRaises(DNSTimeoutError):
                resolve_addrinfo("blackhole.test", 443, timeout=1.0)
            elapsed = time.monotonic() - started

        self.assertLess(
            elapsed, 3.0,
            f"резолв должен уложиться в дедлайн, а занял {elapsed:.1f}с",
        )

    def test_hung_resolution_runs_on_daemon_thread(self) -> None:
        """Залипший резолв не должен мешать процессу завершиться.

        Потоки ``ThreadPoolExecutor`` не демонические, и concurrent.futures
        join-ит их при выходе из интерпретатора — из-за этого приложение
        «просто зависало» при закрытии.
        """
        from utils.net_resolve import DNSTimeoutError, resolve_addrinfo

        with _BlackholeDNS() as blackhole:
            with self.assertRaises(DNSTimeoutError):
                resolve_addrinfo("daemon-check.test", 443, timeout=0.5)

            hung = [
                t for t in threading.enumerate()
                if t.name.startswith("dns-resolve") and t.is_alive()
            ]
            self.assertTrue(hung, "ожидался хотя бы один незавершённый резолв-поток")
            for thread in hung:
                self.assertTrue(
                    thread.daemon,
                    f"поток {thread.name} не демонический — заблокирует выход из процесса",
                )
            blackhole.released.set()

    def test_nxdomain_is_not_reported_as_timeout(self) -> None:
        """Отказ DNS и молчание DNS — разные диагнозы, их нельзя смешивать."""
        import utils.net_resolve as net_resolve
        from utils.net_resolve import DNSTimeoutError, resolve_addrinfo

        original = socket.getaddrinfo
        socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(
            socket.gaierror(socket.EAI_NONAME, "no such host")
        )
        net_resolve.clear_cache()
        try:
            with self.assertRaises(socket.gaierror):
                resolve_addrinfo("nxdomain.test", 443, timeout=5.0)
            self.assertFalse(
                issubclass(DNSTimeoutError, socket.gaierror),
                "DNSTimeoutError не должен выдавать себя за отсутствующий домен",
            )
        finally:
            socket.getaddrinfo = original
            net_resolve.clear_cache()

    def test_concurrent_requests_share_one_lookup(self) -> None:
        """Одинаковые параллельные запросы не должны плодить резолвы."""
        import utils.net_resolve as net_resolve
        from utils.net_resolve import resolve_addrinfo

        calls = []
        original = socket.getaddrinfo

        def _counting(*args, **kwargs):
            calls.append(args)
            time.sleep(0.3)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]

        socket.getaddrinfo = _counting
        net_resolve.clear_cache()
        try:
            results = []
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        resolve_addrinfo("shared.test", 443, timeout=5.0)
                    )
                )
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)

            self.assertEqual(len(results), 4)
            self.assertEqual(len(calls), 1, "ожидался один резолв на четыре запроса")
        finally:
            socket.getaddrinfo = original
            net_resolve.clear_cache()


class PreflightHangRegressionTests(unittest.TestCase):
    def test_check_one_domain_bounded_by_dns_timeout(self) -> None:
        """Раньше: 25с при PREFLIGHT_DNS_TIMEOUT=3 из-за shutdown(wait=True)."""
        import blockcheck.preflight as preflight

        with _BlackholeDNS():
            started = time.monotonic()
            result = preflight.check_one_domain("blackhole.test", cancelled=lambda: False)
            elapsed = time.monotonic() - started

        budget = preflight.PREFLIGHT_DNS_TIMEOUT + 3.0
        self.assertLess(
            elapsed, budget,
            f"preflight домена занял {elapsed:.1f}с при лимите {budget:.1f}с",
        )
        self.assertIsNotNone(result.dns_result)
        self.assertEqual(result.dns_result.error_code, "DNS_TIMEOUT")

    def test_no_pool_is_left_waiting_on_uncancellable_dns(self) -> None:
        """finally не должен ждать задачу, которую нельзя отменить."""
        import inspect

        import blockcheck.preflight as preflight

        source = inspect.getsource(preflight.check_one_domain)
        self.assertIn("shutdown(wait=False", source)
        self.assertNotIn("shutdown(wait=True", source)
        self.assertNotIn("wait=not cancelled", source)

    def test_cancel_stops_preflight_promptly(self) -> None:
        """«Остановить» обязана срабатывать, пока сеть ещё молчит."""
        import blockcheck.preflight as preflight

        stop_at = time.monotonic() + 0.5
        domains = [f"d{i}.test" for i in range(20)]

        with _BlackholeDNS():
            started = time.monotonic()
            preflight.run_preflight(
                domains,
                parallel=4,
                cancelled=lambda: time.monotonic() >= stop_at,
            )
            elapsed = time.monotonic() - started

        self.assertLess(
            elapsed, 4.0,
            f"после отмены run_preflight возвращался {elapsed:.1f}с",
        )


class NoUnboundedResolutionTests(unittest.TestCase):
    """Статический guard: блокирующий резолв не должен вернуться в код."""

    ALLOWED = {
        Path("src/utils/net_resolve.py"),
    }

    def test_no_direct_blocking_resolution_calls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        offenders: list[str] = []

        for path in sorted((root / "src").rglob("*.py")):
            if any(part in {".claude", "__pycache__"} for part in path.parts):
                continue
            if path.relative_to(root) in self.ALLOWED:
                continue

            for lineno, line in enumerate(
                path.read_text(encoding="utf-8-sig").splitlines(), start=1
            ):
                code = line.split("#", 1)[0]
                if "socket.getaddrinfo(" in code or "socket.gethostbyname(" in code:
                    offenders.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")

        self.assertEqual(
            offenders, [],
            "разрешение имён без таймаута снова подвесит приложение; "
            "используйте utils.net_resolve:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
