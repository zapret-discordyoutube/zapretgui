"""Запуск winws2 одной стратегии и настоящее окружение подбора (без Windows)."""

from __future__ import annotations

import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from blockcheck.strategy_search.engine import ScanFatal
from blockcheck.strategy_search.winws_session import WinwsSession


class FakeProcess:
    """Процесс, который печатает строки и живёт, пока его не остановят."""

    def __init__(self, lines: list[bytes], *, exit_after_output: int | None = None, hang_output: bool = True) -> None:
        self._lines = list(lines)
        self._exit_after_output = exit_after_output
        self._hang_output = hang_output
        self.returncode = None
        self.terminated = False
        self._closed = threading.Event()
        self.stdout = self

    def __iter__(self):
        for line in self._lines:
            yield line
        if self._exit_after_output is not None:
            self.returncode = self._exit_after_output
            return
        if self._hang_output:
            self._closed.wait(5)

    def close(self):
        self._closed.set()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1
        self._closed.set()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


class WinwsSessionTests(unittest.TestCase):
    def test_ready_line_makes_start_fast(self) -> None:
        process = FakeProcess([b"github version v1.0.3\n", b"windivert initialized. capture is started.\n"])
        session = WinwsSession(["winws2.exe", "@cfg"], cwd=".", popen=lambda *a, **k: process)

        start = session.start()

        self.assertTrue(start.ok)
        self.assertLess(start.ready_ms, 1500)
        self.assertTrue(session.alive())
        self.assertTrue(session.stop())
        self.assertTrue(process.terminated)

    def test_exit_during_start_is_reported_with_output(self) -> None:
        process = FakeProcess([b"lua: attempt to call nil value 'fakee'\n"], exit_after_output=1)
        session = WinwsSession(["winws2.exe", "@cfg"], cwd=".", popen=lambda *a, **k: process)

        start = session.start()

        self.assertFalse(start.ok)
        self.assertEqual(start.exit_code, 1)
        self.assertIn("fakee", start.output_tail)

    def test_output_is_merged_and_hidden(self) -> None:
        captured = {}

        def popen(command, **kwargs):
            captured.update(kwargs, command=command)
            return FakeProcess([b"windivert initialized\n"])

        session = WinwsSession(["winws2.exe", "@cfg"], cwd="C:/Zapret", popen=popen)
        session.start()
        session.stop()

        self.assertEqual(captured["command"], ["winws2.exe", "@cfg"])
        self.assertEqual(captured["stdout"], subprocess.PIPE)
        self.assertEqual(captured["stderr"], subprocess.STDOUT)
        self.assertEqual(captured["cwd"], "C:/Zapret")


class UdpProbeTests(unittest.TestCase):
    def test_any_udp_reply_means_path_is_open(self) -> None:
        import socket

        from blockcheck.strategy_search import probes, verdict
        from blockcheck.strategy_search.verdict import UdpProbeSpec

        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]

        def reply_garbage():
            data, peer = server.recvfrom(4096)
            server.sendto(b"not an A2S answer", peer)

        thread = threading.Thread(target=reply_garbage, daemon=True)
        thread.start()
        try:
            outcome = probes.probe_udp(UdpProbeSpec("Rust", "source_a2s", "127.0.0.1", port, "127.0.0.1"))
        finally:
            thread.join(2)
            server.close()

        # Сервер ответил не по формату, но ответил: UDP проходит, это не блокировка.
        self.assertEqual(outcome.state, verdict.PROBE_OK)


class RealEnvironmentTests(unittest.TestCase):
    def make_env(self, shutdown=None):
        from blockcheck.strategy_search.environment import RealEnvironment

        return RealEnvironment(shutdown_sync=shutdown or (lambda **_kw: SimpleNamespace(still_running=False)))

    def test_kaspersky_needs_cooldown_between_strategies(self) -> None:
        from blockcheck.strategy_search import environment

        with patch("utils.antivirus_probe.is_kaspersky_present", return_value=True):
            self.assertEqual(environment.strategy_pause_seconds(), environment.KASPERSKY_STRATEGY_COOLDOWN_SECONDS)
        with patch("utils.antivirus_probe.is_kaspersky_present", return_value=False):
            self.assertEqual(environment.strategy_pause_seconds(), 0.0)
        # Не удалось узнать — перестраховка: пауза как с Kaspersky.
        with patch("utils.antivirus_probe.is_kaspersky_present", side_effect=OSError("boom")):
            self.assertEqual(environment.strategy_pause_seconds(), environment.KASPERSKY_STRATEGY_COOLDOWN_SECONDS)

    def test_windivert_readiness_is_checked_before_first_start_only(self) -> None:
        env = self.make_env()
        ready = SimpleNamespace(ready=True, error_code=None, stage="ready")
        with (
            patch("winws_runtime.runtime.system_ops.wait_for_windivert_spawn_ready_runtime", return_value=ready) as wait,
            patch.object(env, "_winws2_path", return_value="winws2.exe"),
            patch("builtins.open"),
        ):
            env.start_session("cfg")
            env.start_session("cfg")
        self.assertEqual(wait.call_count, 1)

    def test_windivert_service_disabled_1058_stops_the_scan(self) -> None:
        env = self.make_env()
        blocked = SimpleNamespace(ready=False, error_code=1058, stage="open")
        with (
            patch("winws_runtime.runtime.system_ops.wait_for_windivert_spawn_ready_runtime", return_value=blocked),
            patch("winws_runtime.runtime.system_ops.aggressive_windivert_cleanup_runtime"),
            patch(
                "winws_runtime.health.windivert_diagnostics.describe_windivert_readiness_failure",
                return_value="служба WinDivert отключена",
            ),
            patch.object(env, "_winws2_path", return_value="winws2.exe"),
        ):
            with self.assertRaises(ScanFatal) as raised:
                env.start_session("cfg")
        self.assertIn("WinDivert", str(raised.exception))

    def test_cleanup_marks_scan_guard_and_stops_zapret(self) -> None:
        calls = []
        env = self.make_env(lambda **kw: calls.append(kw["reason"]) or SimpleNamespace(still_running=False))
        with patch("winws_runtime.runtime.scan_guard.mark_external_winws_scan_active") as guard:
            env.pre_cleanup()
            env.post_cleanup()
        self.assertEqual(calls, ["blockcheck_pre_scan", "blockcheck_post_scan"])
        self.assertEqual([call.args[0] for call in guard.call_args_list], [True, False])

    def test_zapret_that_refuses_to_stop_blocks_the_scan(self) -> None:
        env = self.make_env(lambda **_kw: SimpleNamespace(still_running=True))
        with patch("winws_runtime.runtime.scan_guard.mark_external_winws_scan_active"):
            with self.assertRaises(ScanFatal):
                env.pre_cleanup()


if __name__ == "__main__":
    unittest.main()
