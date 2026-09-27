from __future__ import annotations

import unittest
from unittest.mock import patch

import diagnostics.engine as engine
from diagnostics.verdict import ChannelState, DnsState, judge_channel, judge_dns
from utils.windows_dns_query import DNS_STATUS_NAME_ERROR, DnsAnswer
from utils.windows_http import (
    CERT_FLAG_CN_INVALID,
    ERROR_WINHTTP_CANNOT_CONNECT,
    ERROR_WINHTTP_CONNECTION_ERROR,
    ERROR_WINHTTP_SECURE_FAILURE,
    ERROR_WINHTTP_TIMEOUT,
    SECURITY_CHANNEL_ERROR,
    HttpsResult,
    KIND_CERT,
    KIND_OK,
    KIND_RESET,
    KIND_TLS,
)

DISCORD_REAL = ("162.159.137.232", "162.159.138.232")
# Адрес прокси разблокирующего DNS (Comss/Xbox DNS и т. п.).
UNBLOCK_PROXY = ("83.220.169.155",)


class JudgeDnsTests(unittest.TestCase):
    def _judge(self, **overrides):
        values = dict(
            system_ips=DISCORD_REAL,
            system_status=0,
            reference_ips=DISCORD_REAL,
            hosts_ips=(),
            https_kind=KIND_OK,
        )
        values.update(overrides)
        return judge_dns(**values)

    def test_same_addresses_as_reference_are_ok(self) -> None:
        self.assertEqual(self._judge().state, DnsState.OK)

    def test_unblocking_dns_proxy_with_real_certificate_is_not_spoofing(self) -> None:
        judgement = self._judge(system_ips=UNBLOCK_PROXY, https_kind=KIND_OK)

        self.assertEqual(judgement.state, DnsState.OK)
        self.assertIn("не подмена", judgement.reason)

    def test_foreign_certificate_on_system_address_is_spoofing(self) -> None:
        judgement = self._judge(
            system_ips=("5.6.7.8",),
            https_kind=KIND_CERT,
            https_cert_problem="сертификат выдан другому сайту",
        )

        self.assertEqual(judgement.state, DnsState.SPOOFED)
        self.assertIn("другому сайту", judgement.reason)

    def test_dpi_reset_on_different_address_is_not_called_spoofing(self) -> None:
        judgement = self._judge(system_ips=("5.6.7.8",), https_kind=KIND_RESET)

        self.assertEqual(judgement.state, DnsState.UNKNOWN)

    def test_stub_and_private_addresses_are_spoofing(self) -> None:
        for ip in ("195.82.146.214", "127.0.0.1", "0.0.0.0", "10.10.10.10", "192.168.1.1"):
            with self.subTest(ip=ip):
                self.assertEqual(self._judge(system_ips=(ip,)).state, DnsState.SPOOFED)

    def test_vpn_fake_ip_is_local_not_spoofing(self) -> None:
        self.assertEqual(self._judge(system_ips=("198.18.0.12",)).state, DnsState.LOCAL)

    def test_hosts_file_entry_is_reported_as_local(self) -> None:
        judgement = self._judge(hosts_ips=("1.2.3.4",), system_ips=("1.2.3.4",), https_kind=KIND_CERT)

        self.assertEqual(judgement.state, DnsState.LOCAL)
        self.assertIn("hosts", judgement.reason)

    def test_nxdomain_for_existing_site_is_dns_block(self) -> None:
        judgement = self._judge(system_ips=(), system_status=DNS_STATUS_NAME_ERROR)

        self.assertEqual(judgement.state, DnsState.SPOOFED)

    def test_certificate_checked_on_cached_address_is_not_trusted(self) -> None:
        judgement = self._judge(
            system_ips=("5.6.7.8",),
            https_kind=KIND_OK,
            https_remote_ip=DISCORD_REAL[0],
        )

        self.assertEqual(judgement.state, DnsState.UNKNOWN)
        self.assertIn("кэша Windows", judgement.reason)

    def test_silent_dns_is_unknown(self) -> None:
        judgement = self._judge(system_ips=(), system_status=1460, reference_ips=())

        self.assertEqual(judgement.state, DnsState.UNKNOWN)


class HttpsKindTests(unittest.TestCase):
    def test_secure_failure_with_certificate_flag_is_cert(self) -> None:
        result = HttpsResult(error=ERROR_WINHTTP_SECURE_FAILURE, cert_flags=CERT_FLAG_CN_INVALID)

        self.assertEqual(result.kind, KIND_CERT)
        self.assertEqual(result.cert_problem, "сертификат выдан другому сайту")

    def test_secure_failure_without_certificate_flag_is_tls_break(self) -> None:
        result = HttpsResult(error=ERROR_WINHTTP_SECURE_FAILURE, cert_flags=SECURITY_CHANNEL_ERROR)

        self.assertEqual(result.kind, KIND_TLS)
        self.assertEqual(result.cert_problem, "")

    def test_channel_states(self) -> None:
        self.assertEqual(judge_channel(https_kind=KIND_OK, tcp_ok=False), ChannelState.OK)
        self.assertEqual(
            judge_channel(https_kind=HttpsResult(error=ERROR_WINHTTP_CONNECTION_ERROR).kind, tcp_ok=True),
            ChannelState.DPI,
        )
        self.assertEqual(
            judge_channel(https_kind=HttpsResult(error=ERROR_WINHTTP_TIMEOUT).kind, tcp_ok=True),
            ChannelState.DPI,
        )
        self.assertEqual(
            judge_channel(https_kind=HttpsResult(error=ERROR_WINHTTP_TIMEOUT).kind, tcp_ok=False),
            ChannelState.IP_BLOCK,
        )
        self.assertEqual(
            judge_channel(https_kind=HttpsResult(error=ERROR_WINHTTP_CANNOT_CONNECT).kind, tcp_ok=None),
            ChannelState.IP_BLOCK,
        )


class EngineScenarioTests(unittest.TestCase):
    """Движок целиком, сеть подменена фейками."""

    def _run_dns_check(self, *, system_ips, https: HttpsResult):
        lines: list[str] = []
        with (
            patch.object(engine, "query_ipv4", return_value=DnsAnswer(ips=system_ips)),
            patch.object(engine, "_doh_lookup", return_value=(True, DISCORD_REAL)),
            patch.object(engine, "https_request", return_value=https),
            patch.object(engine, "hosts_file_ipv4", return_value=()),
            patch.object(engine, "system_dns_servers", return_value=("83.220.169.155",)),
        ):
            result = engine.run_dns_check(emit=lines.append)
        return result, "\n".join(lines)

    def test_unblocking_dns_is_not_reported_as_spoofing(self) -> None:
        result, text = self._run_dns_check(system_ips=UNBLOCK_PROXY, https=HttpsResult(status=200))

        self.assertFalse(result["summary"]["dns_poisoning_detected"])
        self.assertNotIn("Обнаружена DNS подмена", text)
        self.assertIn("Comss DNS", text)
        self.assertIn("сам меняет адреса", text)

    def test_foreign_certificate_is_reported_as_spoofing(self) -> None:
        result, text = self._run_dns_check(
            system_ips=("5.6.7.8",),
            https=HttpsResult(error=ERROR_WINHTTP_SECURE_FAILURE, cert_flags=CERT_FLAG_CN_INVALID),
        )

        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("Обнаружена DNS подмена", text)

    def test_connection_test_reports_dpi_and_prints_blocks_in_order(self) -> None:
        lines: list[str] = []
        with (
            patch.object(engine, "query_ipv4", return_value=DnsAnswer(ips=DISCORD_REAL)),
            patch.object(engine, "_doh_lookup", return_value=(True, DISCORD_REAL)),
            patch.object(engine, "https_request", return_value=HttpsResult(error=ERROR_WINHTTP_CONNECTION_ERROR)),
            patch.object(engine, "hosts_file_ipv4", return_value=()),
            patch.object(engine, "system_dns_servers", return_value=()),
            patch.object(engine, "_tcp_connect", side_effect=lambda ip, _t: engine._TcpResult(True, ip, 20.0)),
            patch.object(engine, "_ping", return_value=None),
            patch.object(engine, "_zapret_status", return_value=(False, "❌ Zapret не запущен")),
            patch.object(engine, "_discover_googlevideo", return_value=("rr1---sn-test.googlevideo.com", "")),
        ):
            result = engine.run_connection_test("discord", emit=lines.append)

        text = "\n".join(lines)
        self.assertFalse(result["dns_poisoning_detected"])
        self.assertIn("соединение режет DPI провайдера. Запустите Zapret", text)
        hosts_order = [line.split(" ")[0] for line in lines if line.endswith(("вход", "статусы", "файлы"))]
        self.assertEqual(hosts_order, ["discord.com", "gateway.discord.gg", "cdn.discordapp.com"])

    def test_run_deadline_still_prints_summary_and_keeps_spoofing(self) -> None:
        import time

        def _slow_https(*_args, **_kwargs):
            time.sleep(0.6)
            return HttpsResult(error=12017)

        lines: list[str] = []
        with (
            patch.object(engine, "RUN_DEADLINE", 0.2),
            patch.object(engine, "query_ipv4", return_value=DnsAnswer(ips=("195.82.146.214",))),
            patch.object(engine, "_doh_lookup", return_value=(True, DISCORD_REAL)),
            patch.object(engine, "https_request", side_effect=_slow_https),
            patch.object(engine, "hosts_file_ipv4", return_value=()),
            patch.object(engine, "system_dns_servers", return_value=()),
        ):
            result = engine.run_dns_check(emit=lines.append)

        text = "\n".join(lines)
        self.assertNotIn("stopped", result)
        self.assertTrue(result["summary"]["dns_poisoning_detected"])
        self.assertIn("Итог", text)
        self.assertIn("не уложилась", text)

    def test_stop_returns_quickly_and_marks_stopped(self) -> None:
        import threading

        release = threading.Event()

        def _slow_https(*_args, **_kwargs):
            release.wait(5)
            return HttpsResult(status=200)

        with (
            patch.object(engine, "query_ipv4", return_value=DnsAnswer(ips=DISCORD_REAL)),
            patch.object(engine, "_doh_lookup", return_value=(True, DISCORD_REAL)),
            patch.object(engine, "https_request", side_effect=_slow_https),
            patch.object(engine, "hosts_file_ipv4", return_value=()),
            patch.object(engine, "system_dns_servers", return_value=()),
        ):
            result = engine.run_dns_check(emit=lambda _line: None, should_stop=lambda: True)
        release.set()

        self.assertTrue(result.get("stopped"))


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
