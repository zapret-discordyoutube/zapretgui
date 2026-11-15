"""The ``netprobe`` interface: Linux backend (real loopback where the kernel allows it), stub, neutral hosts lookup."""

from __future__ import annotations

import socket
import struct
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from platforms import current
from platforms.linux import netprobe as linux_netprobe
from platforms.linux.netprobe import LinuxNetProbe, _answer_from, _checksum, _echo_packet, parse_icmp_error
from platforms.netprobe_types import (
    DNS_STATUS_NAME_ERROR,
    DNS_STATUS_NO_RECORDS,
    DNS_STATUS_REFUSED,
    DNS_STATUS_SERVER_FAILURE,
    ERROR_CANCELLED,
    ERROR_SUCCESS,
    ERROR_TIMEOUT,
    HOP_ROUTER,
    HOP_SILENT,
    HOP_TARGET,
    HOP_UNSUPPORTED,
    DnsAnswer,
)
from platforms.stub import PLATFORM as STUB
from utils import dns_wire

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux sockets")


def _icmp_allowed() -> bool:
    sock = linux_netprobe._open_ping_socket(socket.AF_INET)
    if sock is None:
        return False
    sock.close()
    return True


needs_icmp = pytest.mark.skipif(not (sys.platform.startswith("linux") and _icmp_allowed()), reason="ICMP sockets not allowed")


# -- the backends ---------------------------------------------------------------------------------


def test_every_backend_has_netprobe() -> None:
    from platforms.linux import PLATFORM as LINUX
    from platforms.windows import PLATFORM as WINDOWS

    for backend in (WINDOWS, LINUX, STUB):
        assert backend.netprobe is not None
    assert WINDOWS.netprobe.hosts_file_path().replace("/", "\\").lower().endswith("system32\\drivers\\etc\\hosts")
    assert LINUX.netprobe.hosts_file_path() == "/etc/hosts"


def test_stub_answers_safely_without_touching_the_network() -> None:
    probe = STUB.netprobe
    assert probe.ping_ipv4_host("example.com", count=2, timeout_ms=10).error_code == "UNSUPPORTED"
    assert probe.ping_ipv6("::1", count=2, timeout_ms=10).error_code == "UNSUPPORTED"
    assert probe.trace_hop_ipv4("1.1.1.1", 3, timeout_ms=10).kind == HOP_UNSUPPORTED
    assert probe.query_ipv4("example.com", timeout=1).ips == ()
    assert probe.system_dns_servers() == () and probe.tcp_server_window("1.1.1.1") is None


def test_the_test_session_does_not_measure_the_real_network() -> None:
    """tests/conftest.py replaces the probes of the running platform (outside Windows) with the stub."""
    if sys.platform == "win32":
        pytest.skip("Windows measures for real")
    assert current().netprobe.ping_ipv4_host("127.0.0.1", count=1, timeout_ms=10).error_code == "UNSUPPORTED"


# -- ping -----------------------------------------------------------------------------------------


def test_checksum_matches_the_rfc_1071_example() -> None:
    # RFC 1071 section 3: the words 0001 f203 f4f5 f6f7 sum to ddf2 (complement 220d).
    assert _checksum(bytes.fromhex("0001f203f4f5f6f7")) == 0x220D


def test_echo_packet_has_a_valid_checksum() -> None:
    packet = _echo_packet(8, 7)
    assert packet[0] == 8 and struct.unpack("!H", packet[6:8])[0] == 7
    assert _checksum(packet) == 0  # a correct packet sums to zero


@needs_icmp
def test_ping_loopback_answers_with_times_and_ttl() -> None:
    result = LinuxNetProbe().ping_ipv4_host("127.0.0.1", count=3, timeout_ms=1000, resolved_ip="127.0.0.1")
    assert result.ok and result.sent == 3 and result.received == 3
    assert 0 <= result.min_ms <= result.average_ms <= result.max_ms < 1000
    assert result.resolved_ip == "127.0.0.1" and result.ttl and 0 < result.ttl <= 255


@needs_icmp
def test_ping_ipv6_loopback() -> None:
    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    result = LinuxNetProbe().ping_ipv6("::1", count=2, timeout_ms=1000)
    if not result.ok and result.error_code == "TIMEOUT":
        pytest.skip("IPv6 loopback is switched off here")
    assert result.ok and result.received == 2


@needs_icmp
def test_ping_stops_between_packets_when_cancelled() -> None:
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # the first packet goes, the second is cancelled

    result = LinuxNetProbe().ping_ipv4_host("127.0.0.1", count=5, timeout_ms=500, resolved_ip="127.0.0.1", cancelled=cancelled)
    assert result.sent == 1 and result.received == 1


def test_ping_reports_a_failed_name_lookup() -> None:
    with patch("utils.net_resolve.resolve_ipv4", return_value=None):
        result = LinuxNetProbe().ping_ipv4_host("no.such.invalid", count=2, timeout_ms=500)
    assert not result.ok and result.error_code == "DNS_ERR" and result.sent == 2


def test_ping_falls_back_to_the_ping_program_when_the_icmp_socket_is_refused() -> None:
    done = MagicMock(stdout="64 bytes from 10.0.0.1: icmp_seq=1 ttl=57 time=12.5 ms\n64 bytes from 10.0.0.1: icmp_seq=2 ttl=57 time=14.5 ms\n")
    with (
        patch.object(linux_netprobe, "_open_ping_socket", return_value=None),
        patch("shutil.which", return_value="/bin/ping"),
        patch("subprocess.run", return_value=done) as run,
    ):
        result = LinuxNetProbe().ping_ipv4_host("10.0.0.1", count=2, timeout_ms=1000, resolved_ip="10.0.0.1")
    assert result.ok and result.received == 2 and result.ttl == 57
    assert result.average_ms == pytest.approx(13.5) and result.min_ms == 12.5 and result.max_ms == 14.5
    assert run.call_args.args[0][:4] == ["/bin/ping", "-n", "-c", "2"]


def test_ping_without_socket_and_without_program_is_unsupported_not_a_crash() -> None:
    with patch.object(linux_netprobe, "_open_ping_socket", return_value=None), patch("shutil.which", return_value=None):
        result = LinuxNetProbe().ping_ipv4_host("10.0.0.1", count=2, timeout_ms=100, resolved_ip="10.0.0.1")
    assert not result.ok and result.error_code == "UNSUPPORTED"


# -- trace ----------------------------------------------------------------------------------------


@needs_icmp
def test_trace_hop_to_loopback_reaches_the_target() -> None:
    hop = LinuxNetProbe().trace_hop_ipv4("127.0.0.1", 5, timeout_ms=1000)
    assert hop.kind == HOP_TARGET and hop.address == "127.0.0.1" and hop.rtt_ms is not None


def test_trace_hop_without_an_icmp_socket_is_unsupported() -> None:
    with patch.object(linux_netprobe, "_open_ping_socket", return_value=None):
        assert LinuxNetProbe().trace_hop_ipv4("1.1.1.1", 3, timeout_ms=100).kind == HOP_UNSUPPORTED


def _error_queue_message(icmp_type: int, address: str, origin: int = 2) -> list:
    extended = struct.pack("=IBBBBII", 113, origin, icmp_type, 0, 0, 0, 0)
    offender = struct.pack("=HH4s8x", socket.AF_INET, 0, socket.inet_aton(address))
    return [(socket.SOL_IP, linux_netprobe._IP_RECVERR, extended + offender)]


def test_icmp_error_from_the_error_queue_names_the_router() -> None:
    assert parse_icmp_error(_error_queue_message(11, "203.0.113.7")) == (11, 0, "203.0.113.7")


def test_errors_that_are_not_icmp_are_ignored() -> None:
    assert parse_icmp_error(_error_queue_message(11, "203.0.113.7", origin=1)) is None
    assert parse_icmp_error([]) is None
    assert parse_icmp_error([(socket.SOL_IP, linux_netprobe._IP_RECVERR, b"short")]) is None


class _FakeTraceSocket:
    """A ping socket whose error queue holds one 'time exceeded' message from a router."""

    def __init__(self, message) -> None:
        self.message = message
        self.options: list[tuple] = []

    def setsockopt(self, *args) -> None:
        self.options.append(args)

    def sendto(self, *_args) -> None:
        pass

    def fileno(self) -> int:
        return 0

    def recvmsg(self, _size, _ancillary, flags=0):
        assert flags == socket.MSG_ERRQUEUE
        return b"", self.message, 0, None

    def close(self) -> None:
        pass


def test_trace_hop_reports_the_router_that_dropped_the_packet() -> None:
    fake = _FakeTraceSocket(_error_queue_message(11, "198.51.100.1"))
    poller = MagicMock()
    poller.poll.return_value = [(0, linux_netprobe.select.POLLERR)]
    with patch.object(linux_netprobe, "_open_ping_socket", return_value=fake), patch.object(
        linux_netprobe.select, "poll", return_value=poller
    ):
        hop = LinuxNetProbe().trace_hop_ipv4("93.184.216.34", 4, timeout_ms=500)
    assert hop.kind == HOP_ROUTER and hop.address == "198.51.100.1"
    assert (socket.SOL_IP, socket.IP_TTL, 4) in fake.options


def test_trace_hop_is_silent_when_nobody_answers() -> None:
    fake = _FakeTraceSocket([])
    poller = MagicMock()
    poller.poll.return_value = []
    with patch.object(linux_netprobe, "_open_ping_socket", return_value=fake), patch.object(
        linux_netprobe.select, "poll", return_value=poller
    ):
        assert LinuxNetProbe().trace_hop_ipv4("93.184.216.34", 9, timeout_ms=50).kind == HOP_SILENT


# -- DNS ------------------------------------------------------------------------------------------


def _result(rcode, *records, status=dns_wire.STATUS_OK, failure=""):
    return dns_wire.DnsQueryResult(status=status, rcode=rcode, records=tuple(records), failure=failure)


def _a(value):
    return dns_wire.DnsRecord("x.test", dns_wire.TYPE_A, 60, value)


def test_dns_response_codes_are_mapped_onto_the_windows_vocabulary() -> None:
    cname = dns_wire.DnsRecord("x.test", dns_wire.TYPE_CNAME, 60, "y.test")
    ok = _answer_from(_result(0, cname, _a("1.2.3.4"), _a("1.2.3.5")), 12.0)
    assert ok.ips == ("1.2.3.4", "1.2.3.5") and ok.cnames == ("y.test",) and ok.status == ERROR_SUCCESS and ok.ok
    assert _answer_from(_result(0), 1.0).status == DNS_STATUS_NO_RECORDS
    assert _answer_from(_result(3), 1.0).status == DNS_STATUS_NAME_ERROR
    assert _answer_from(_result(5), 1.0).status == DNS_STATUS_REFUSED
    assert _answer_from(_result(2), 1.0).status == DNS_STATUS_SERVER_FAILURE
    silent = _result(None, status=dns_wire.STATUS_TIMEOUT, failure=dns_wire.FAILURE_TIMEOUT)
    assert _answer_from(silent, 1.0).status == ERROR_TIMEOUT
    stopped = _result(None, status=dns_wire.STATUS_ERROR, failure=dns_wire.FAILURE_CANCELLED)
    assert _answer_from(stopped, 1.0).status == ERROR_CANCELLED
    assert _answer_from(None, 1.0).status == ERROR_TIMEOUT


def test_query_asks_each_system_server_until_one_answers() -> None:
    asked = []

    def fake_query(server, name, rtype, **_kw):
        asked.append(server)
        if server == "10.0.0.1":
            return _result(None, status=dns_wire.STATUS_TIMEOUT, failure=dns_wire.FAILURE_TIMEOUT)
        return _result(0, _a("93.184.216.34"))

    probe = LinuxNetProbe()
    with patch.object(probe, "system_dns_servers", return_value=("10.0.0.1", "10.0.0.2", "10.0.0.3")), patch.object(
        dns_wire, "query_server", side_effect=fake_query
    ):
        answer = probe.query_ipv4("example.com.", timeout=5)
    assert asked == ["10.0.0.1", "10.0.0.2"] and answer.ips == ("93.184.216.34",) and answer.status == ERROR_SUCCESS


def test_query_with_a_name_but_no_servers_or_no_name() -> None:
    probe = LinuxNetProbe()
    assert probe.query_ipv4("", timeout=1).status == DNS_STATUS_NAME_ERROR
    with patch.object(probe, "system_dns_servers", return_value=()):
        answer = probe.query_ipv4("example.com", timeout=1)
    assert answer.ips == () and answer.status == -1


def test_query_can_be_cancelled_and_times_out_without_waiting_for_a_hung_server() -> None:
    gate = threading.Event()

    def hung(server, name, rtype, *, cancel=None, **_kw):
        gate.wait(5)
        return _result(None, status=dns_wire.STATUS_ERROR, failure=dns_wire.FAILURE_CANCELLED)

    probe = LinuxNetProbe()
    try:
        with patch.object(probe, "system_dns_servers", return_value=("10.0.0.1",)), patch.object(
            dns_wire, "query_server", side_effect=hung
        ):
            started = time.perf_counter()
            assert probe.query_ipv4("example.com", timeout=5, cancelled=lambda: True).status == ERROR_CANCELLED
            assert time.perf_counter() - started < 1.0
            started = time.perf_counter()
            assert probe.query_ipv4("example.com", timeout=0.2).status == ERROR_TIMEOUT
            assert time.perf_counter() - started < 1.0
    finally:
        gate.set()


def test_system_dns_servers_reads_resolv_conf_and_prefers_upstream_behind_a_local_stub(tmp_path) -> None:
    plain = tmp_path / "resolv.conf"
    plain.write_text("# c\nnameserver 192.168.1.1\nnameserver fe80::1\nnameserver 192.168.1.1\nsearch lan\n")
    stub = tmp_path / "stub.conf"
    stub.write_text("nameserver 127.0.0.53\n")
    real = tmp_path / "real.conf"
    real.write_text("nameserver 9.9.9.9\nnameserver 149.112.112.112\n")
    assert linux_netprobe._read_nameservers(str(plain)) == ("192.168.1.1",)
    assert linux_netprobe._read_nameservers(str(tmp_path / "missing")) == ()

    def fake_read(path):
        return {"/etc/resolv.conf": ("127.0.0.53",), "/run/systemd/resolve/resolv.conf": ("9.9.9.9", "149.112.112.112")}.get(path, ())

    with patch.object(linux_netprobe, "_read_nameservers", side_effect=fake_read):
        assert LinuxNetProbe().system_dns_servers() == ("9.9.9.9", "149.112.112.112")
    with patch.object(linux_netprobe, "_read_nameservers", side_effect=lambda p: ("127.0.0.53",) if p == "/etc/resolv.conf" else ()):
        assert LinuxNetProbe().system_dns_servers() == ("127.0.0.53",)


# -- TCP window and hosts -------------------------------------------------------------------------


class _FakeConnection:
    def __init__(self, info: bytes) -> None:
        self.info = info

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def getsockopt(self, level, name, size):
        assert (level, name) == (socket.IPPROTO_TCP, socket.TCP_INFO)
        return self.info


def test_tcp_window_is_read_from_the_tcp_info_block() -> None:
    info = bytearray(232)
    struct.pack_into("=I", info, 228, 65535)
    with patch("socket.create_connection", return_value=_FakeConnection(bytes(info))):
        assert LinuxNetProbe().tcp_server_window("203.0.113.1") == 65535


def test_tcp_window_is_unknown_on_kernels_that_do_not_report_it() -> None:
    with patch("socket.create_connection", return_value=_FakeConnection(bytes(104))):
        assert LinuxNetProbe().tcp_server_window("203.0.113.1") is None
    with patch("socket.create_connection", side_effect=OSError("down")):
        assert LinuxNetProbe().tcp_server_window("203.0.113.1") is None


def test_hosts_lookup_reads_names_in_any_case_with_comments(tmp_path) -> None:
    from utils.hosts_lookup import hosts_file_addresses, hosts_file_ipv4

    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n# comment\n10.1.1.1 Foo.Example  bar # tail\n::1 foo.example\nbad.line\n999.1.1.1.1 foo.example\n")
    assert hosts_file_addresses("FOO.example.", path=str(hosts)) == ("10.1.1.1", "::1")
    assert hosts_file_ipv4("foo.example", path=str(hosts)) == ("10.1.1.1",)
    assert hosts_file_addresses("", path=str(hosts)) == ()
    assert hosts_file_addresses("x", path=str(tmp_path / "missing")) == ()


def test_hosts_lookup_uses_the_platform_hosts_path(tmp_path) -> None:
    from utils.hosts_lookup import hosts_file_ipv4

    hosts = tmp_path / "hosts"
    hosts.write_text("10.2.2.2 zapret.test\n")
    with patch.object(current().netprobe, "hosts_file_path", return_value=str(hosts)):
        assert hosts_file_ipv4("zapret.test") == ("10.2.2.2",)
