from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest.mock import patch

import diagnostics.engine as engine
from diagnostics.tls_probe import (
    KIND_CERT,
    KIND_CONNECT,
    KIND_OK,
    KIND_RESET,
    KIND_TIMEOUT,
    KIND_TLS,
    ProbeResult,
    https_get,
)
from diagnostics.verdict import (
    DnsState,
    Level,
    ReachState,
    TargetOutcome,
    judge_dns,
    judge_reach,
    summarize_service,
)
from utils.windows_dns_query import DNS_STATUS_NAME_ERROR, DnsAnswer

DISCORD_REAL = ("162.159.137.232", "162.159.138.232")
# Адрес прокси разблокирующего DNS (Comss/Xbox DNS и т. п.).
UNBLOCK_PROXY = ("83.220.169.155",)


def _ok(ip: str = DISCORD_REAL[0], **extra) -> ProbeResult:
    return ProbeResult(ip=ip, kind=KIND_OK, status=200, tls_version="TLSv1.3", **extra)


class JudgeDnsTests(unittest.TestCase):
    def _judge(self, **overrides):
        values = dict(
            system_ips=DISCORD_REAL,
            system_status=0,
            reference_ips=DISCORD_REAL,
            hosts_ips=(),
            check_kind=KIND_OK,
        )
        values.update(overrides)
        return judge_dns(**values)

    def test_same_addresses_as_reference_are_ok(self) -> None:
        self.assertEqual(self._judge(check_kind="").state, DnsState.OK)

    def test_unblocking_dns_proxy_with_real_certificate_is_not_spoofing(self) -> None:
        judgement = self._judge(system_ips=UNBLOCK_PROXY, check_kind=KIND_OK)

        self.assertEqual(judgement.state, DnsState.OK)
        self.assertIn("не подмена", judgement.reason)

    def test_foreign_certificate_on_system_address_is_spoofing(self) -> None:
        judgement = self._judge(
            system_ips=("5.6.7.8",),
            check_kind=KIND_CERT,
            check_cert_problem="сертификат выдан другому сайту",
        )

        self.assertEqual(judgement.state, DnsState.SPOOFED)
        self.assertIn("другому сайту", judgement.reason)

    def test_dpi_reset_on_different_address_is_not_called_spoofing(self) -> None:
        judgement = self._judge(system_ips=("5.6.7.8",), check_kind=KIND_RESET)

        self.assertEqual(judgement.state, DnsState.UNKNOWN)

    def test_stub_and_private_addresses_are_spoofing(self) -> None:
        for ip in ("195.82.146.214", "127.0.0.1", "0.0.0.0", "10.10.10.10", "192.168.1.1"):
            with self.subTest(ip=ip):
                self.assertEqual(self._judge(system_ips=(ip,)).state, DnsState.SPOOFED)

    def test_vpn_fake_ip_is_local_not_spoofing(self) -> None:
        self.assertEqual(self._judge(system_ips=("198.18.0.12",)).state, DnsState.LOCAL)

    def test_hosts_file_entry_is_reported_as_local(self) -> None:
        judgement = self._judge(hosts_ips=("1.2.3.4",), system_ips=("1.2.3.4",), check_kind=KIND_CERT)

        self.assertEqual(judgement.state, DnsState.LOCAL)
        self.assertIn("hosts", judgement.reason)

    def test_nxdomain_for_existing_site_is_dns_block(self) -> None:
        judgement = self._judge(system_ips=(), system_status=DNS_STATUS_NAME_ERROR, check_kind="")

        self.assertEqual(judgement.state, DnsState.SPOOFED)

    def test_silent_dns_is_unknown(self) -> None:
        judgement = self._judge(system_ips=(), system_status=1460, reference_ips=(), check_kind="")

        self.assertEqual(judgement.state, DnsState.UNKNOWN)


class ReachTests(unittest.TestCase):
    def test_reach_states(self) -> None:
        self.assertEqual(judge_reach(_ok()), ReachState.OK)
        self.assertEqual(judge_reach(None), ReachState.NO_ADDRESS)
        for kind in (KIND_RESET, KIND_TLS, KIND_TIMEOUT):
            with self.subTest(kind=kind):
                self.assertEqual(judge_reach(ProbeResult(ip="1.1.1.1", kind=kind)), ReachState.DPI)
        self.assertEqual(judge_reach(ProbeResult(ip="1.1.1.1", kind=KIND_CONNECT)), ReachState.IP_BLOCK)
        self.assertEqual(judge_reach(ProbeResult(ip="1.1.1.1", kind=KIND_CERT)), ReachState.CERT)

    def test_body_cut_at_16kb_is_freeze_but_short_page_is_ok(self) -> None:
        self.assertEqual(judge_reach(_ok(body_cut=True, body_size=16_500)), ReachState.FREEZE)
        self.assertEqual(judge_reach(_ok(body_cut=True, body_size=3_000)), ReachState.OK)
        self.assertEqual(judge_reach(_ok(body_size=16_500)), ReachState.OK)


class ServiceSummaryTests(unittest.TestCase):
    def _outcomes(self, main: ReachState, *others: ReachState, dns: DnsState = DnsState.OK):
        items = [TargetOutcome("www.youtube.com", "сайт", main, dns, main=True)]
        for index, state in enumerate(others):
            items.append(TargetOutcome(f"host{index}", f"часть {index}", state, DnsState.OK))
        return items

    def test_site_that_opens_is_never_red_even_with_dns_block(self) -> None:
        verdict = summarize_service(
            "YouTube",
            self._outcomes(ReachState.OK, ReachState.OK, dns=DnsState.SPOOFED),
            zapret_running=True,
        )

        self.assertEqual(verdict.level, Level.WARN)
        self.assertEqual(verdict.headline, "YouTube открывается")
        self.assertIn("www.youtube.com", verdict.dns_note)
        self.assertTrue(any("DoH" in item for item in verdict.advice))

    def test_everything_open_is_ok(self) -> None:
        verdict = summarize_service("Discord", self._outcomes(ReachState.OK, ReachState.OK), zapret_running=True)

        self.assertEqual(verdict.level, Level.OK)
        self.assertEqual(verdict.advice, ())

    def test_main_blocked_by_dpi_with_zapret_running_suggests_strategy(self) -> None:
        verdict = summarize_service("Discord", self._outcomes(ReachState.DPI), zapret_running=True)

        self.assertEqual(verdict.level, Level.FAIL)
        self.assertIn("не помогает", verdict.headline)
        self.assertIn("Подбор стратегии", verdict.advice[0])

    def test_main_blocked_without_zapret_suggests_start(self) -> None:
        verdict = summarize_service("Discord", self._outcomes(ReachState.DPI), zapret_running=False)

        self.assertIn("Запустите Zapret", verdict.advice[0])

    def test_broken_secondary_part_is_named(self) -> None:
        verdict = summarize_service(
            "YouTube", self._outcomes(ReachState.OK, ReachState.FREEZE), zapret_running=True
        )

        self.assertEqual(verdict.level, Level.WARN)
        self.assertIn("часть 0", verdict.headline)


class _Net:
    """Фейковая сеть для движка: DNS, эталон и HTTPS по адресу."""

    def __init__(self, *, system=DISCORD_REAL, reference=DISCORD_REAL, status=0, https=None):
        self.system = system
        self.reference = reference
        self.status = status
        self.https = https or (lambda host, ip: _ok(ip))
        self.calls: list[tuple[str, str]] = []

    def patches(self):
        def _https_get(host, ip, *_args, **_kwargs):
            self.calls.append((host, ip))
            return self.https(host, ip)

        return (
            patch.object(engine, "query_ipv4", return_value=DnsAnswer(ips=self.system, status=self.status)),
            patch.object(engine, "_doh_lookup", return_value=(True, self.reference)),
            patch.object(engine, "https_get", side_effect=_https_get),
            patch.object(engine, "hosts_file_ipv4", return_value=()),
            patch.object(engine, "system_dns_servers", return_value=("83.220.169.155",)),
            patch.object(engine, "_zapret_status", return_value=(True, "✅ Zapret запущен")),
            patch.object(engine, "_discover_googlevideo", return_value=("rr1---sn-test.googlevideo.com", "")),
        )

    def run(self, fn, *args, **kwargs):
        from contextlib import ExitStack

        with ExitStack() as stack:
            for item in self.patches():
                stack.enter_context(item)
            return fn(*args, **kwargs)


class EngineScenarioTests(unittest.TestCase):
    """Движок целиком, сеть подменена фейками."""

    def test_unblocking_dns_is_not_reported_as_spoofing(self) -> None:
        lines: list[str] = []
        net = _Net(system=UNBLOCK_PROXY)
        result = net.run(engine.run_dns_check, emit=lines.append)
        text = "\n".join(lines)

        self.assertFalse(result["summary"]["dns_poisoning_detected"])
        self.assertNotIn("Обнаружена DNS подмена", text)
        self.assertIn("Comss DNS", text)
        self.assertIn("сам меняет адреса", text)

    def test_dns_tab_skips_https_when_address_matches_reference(self) -> None:
        net = _Net()
        net.run(engine.run_dns_check, emit=lambda _line: None)

        self.assertEqual(net.calls, [])

    def test_foreign_certificate_is_reported_as_spoofing(self) -> None:
        lines: list[str] = []
        net = _Net(
            system=("5.6.7.8",),
            https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_CERT, cert_problem="сертификат выдан другому сайту"),
        )
        result = net.run(engine.run_dns_check, emit=lines.append)

        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("Обнаружена DNS подмена", "\n".join(lines))

    def test_nxdomain_site_that_opens_by_real_address_is_reported_as_working(self) -> None:
        """Случай из жалобы: DNS говорит «домена нет», браузер с DoH открывает сайт."""
        lines: list[str] = []
        net = _Net(system=(), status=DNS_STATUS_NAME_ERROR, reference=("142.251.157.4",))
        result = net.run(engine.run_connection_test, "youtube", emit=lines.append)
        text = "\n".join(lines)

        youtube = result["services"][0]
        self.assertEqual(youtube["headline"], "YouTube открывается")
        self.assertEqual(youtube["level"], "warn")
        self.assertIn("www.youtube.com", youtube["dns_note"])
        self.assertTrue(result["dns_poisoning_detected"])
        self.assertIn(("www.youtube.com", "142.251.157.4"), net.calls)
        self.assertNotIn("❌ YouTube", text)

    def test_dpi_reset_retries_once_on_another_address_and_reports_strategy(self) -> None:
        lines: list[str] = []
        net = _Net(https=lambda host, ip: ProbeResult(ip=ip, kind=KIND_RESET))
        result = net.run(engine.run_connection_test, "discord", emit=lines.append)
        text = "\n".join(lines)

        discord_calls = [ip for host, ip in net.calls if host == "discord.com"]
        self.assertEqual(sorted(discord_calls), sorted(DISCORD_REAL))
        self.assertFalse(result["dns_poisoning_detected"])
        self.assertEqual(result["services"][0]["level"], "fail")
        self.assertIn("Подбор стратегии", text)
        hosts_order = [line.split(" ")[1] for line in lines if line.startswith("❌ ") and "." in line.split(" ")[1]]
        self.assertEqual(hosts_order, ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"])

    def test_system_address_that_fails_is_rechecked_by_reference_address(self) -> None:
        def _https(host, ip):
            if ip == "5.6.7.8":
                return ProbeResult(ip=ip, kind=KIND_RESET)
            return _ok(ip)

        net = _Net(system=("5.6.7.8",), https=_https)
        result = net.run(engine.run_connection_test, "discord", emit=lambda _line: None)

        self.assertEqual(result["services"][0]["level"], "ok")
        self.assertIn(("discord.com", DISCORD_REAL[0]), net.calls)

    def test_run_deadline_still_prints_summary_and_keeps_spoofing(self) -> None:
        def _slow(host, ip):
            time.sleep(0.6)
            return ProbeResult(ip=ip, kind="cancelled")

        lines: list[str] = []
        net = _Net(system=("195.82.146.214",), https=_slow)
        with patch.object(engine, "RUN_DEADLINE", 0.2):
            result = net.run(engine.run_dns_check, emit=lines.append)

        text = "\n".join(lines)
        self.assertNotIn("stopped", result)
        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("Итог", text)
        self.assertIn("не уложилась", text)

    def test_stop_returns_quickly_and_marks_stopped(self) -> None:
        release = threading.Event()

        def _slow(host, ip):
            release.wait(5)
            return _ok(ip)

        net = _Net(system=("5.6.7.8",), https=_slow)
        started = time.monotonic()
        result = net.run(engine.run_dns_check, emit=lambda _line: None, should_stop=lambda: True)
        release.set()

        self.assertTrue(result.get("stopped"))
        self.assertLess(time.monotonic() - started, 2.0)


class TlsProbeFailureTests(unittest.TestCase):
    """Классификация сбоев на настоящих сокетах (без TLS-сервера)."""

    def _server(self, behaviour):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def _serve():
            try:
                conn, _ = listener.accept()
                behaviour(conn)
            except OSError:
                pass
            finally:
                listener.close()

        threading.Thread(target=_serve, daemon=True).start()
        return port

    def _get(self, port, timeout=1.0):
        return https_get("example.com", "127.0.0.1", timeout=timeout, port=port)

    def test_reset_during_handshake_is_reset_or_tls(self) -> None:
        def _rst(conn):
            conn.recv(1024)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            conn.close()

        result = self._get(self._server(_rst))

        self.assertIn(result.kind, (KIND_RESET, KIND_TLS))
        self.assertEqual(judge_reach(result), ReachState.DPI)

    def test_silent_server_after_connect_is_timeout(self) -> None:
        def _silent(conn):
            time.sleep(2)
            conn.close()

        result = self._get(self._server(_silent), timeout=0.5)

        self.assertEqual(result.kind, KIND_TIMEOUT)
        self.assertEqual(result.stage, "tls")

    def test_closed_port_is_connect_failure(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        result = self._get(port)

        self.assertEqual(result.kind, KIND_CONNECT)
        self.assertEqual(judge_reach(result), ReachState.IP_BLOCK)


class IcmpAddressTests(unittest.TestCase):
    def test_icmp_address_keeps_network_byte_order_in_memory(self) -> None:
        import socket
        import struct

        from utils.windows_icmp import _ipv4_to_dword

        for ip in ("142.251.150.4", "162.159.137.232"):
            with self.subTest(ip=ip):
                self.assertEqual(struct.pack("<I", _ipv4_to_dword(ip)), socket.inet_aton(ip))


if __name__ == "__main__":
    unittest.main()
