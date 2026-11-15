"""Network measurements on Linux without root and without helper programs.

* Ping and trace: an unprivileged ICMP socket (``SOCK_DGRAM`` + ``IPPROTO_ICMP``). The kernel allows it
  to groups listed in ``net.ipv4.ping_group_range`` (Debian 12/13 and Ubuntu 24.04 allow everybody).
  When the socket is refused, ping falls back to the ``ping`` program.
* DNS: our own wire client (``utils.dns_wire``) against the servers of ``/etc/resolv.conf``: no cache, no hosts.
* TCP window: ``TCP_INFO`` (kernel 6.2+ reports ``tcpi_snd_wnd``; older kernels answer None).
"""

from __future__ import annotations

import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable

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
    PingResult,
    TraceHopResult,
    status_text,
)

_ICMP_ECHO_REQUEST = 8
_ICMP_ECHO_REPLY = 0
_ICMP_TIME_EXCEEDED = 11
_ICMP6_ECHO_REQUEST = 128
_ICMP6_ECHO_REPLY = 129
_IP_RECVERR = 11  # <linux/in.h>: not exported by every Python build
_IP_RECVTTL = 12  # same: asks the kernel to hand over the TTL of a reply as ancillary data
_SO_EE_ORIGIN_ICMP = 2
_PAYLOAD = b"zapret"
_TCPI_SND_WND_OFFSET = 228  # offset of tcpi_snd_wnd in struct tcp_info (<linux/tcp.h>)


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def _echo_packet(kind: int, sequence: int) -> bytes:
    header = struct.pack("!BBHHH", kind, 0, 0, 0, sequence)  # the kernel sets the id of a ping socket
    checksum = _checksum(header + _PAYLOAD)
    return struct.pack("!BBHHH", kind, 0, checksum, 0, sequence) + _PAYLOAD


def _open_ping_socket(family: int) -> socket.socket | None:
    proto = socket.IPPROTO_ICMP if family == socket.AF_INET else socket.IPPROTO_ICMPV6
    try:
        return socket.socket(family, socket.SOCK_DGRAM, proto)
    except (PermissionError, OSError):
        return None


def _received_ttl(ancillary) -> int | None:
    for level, kind, data in ancillary:
        if level == socket.SOL_IP and kind == socket.IP_TTL and data:
            return int.from_bytes(data[:4], sys.byteorder)
    return None


def parse_icmp_error(ancillary) -> tuple[int, int, str] | None:
    """(ICMP type, ICMP code, address of the host that reported it) from the socket error queue, or None."""
    for level, kind, data in ancillary:
        if level == socket.SOL_IP and kind == _IP_RECVERR and len(data) >= 16 + 8:
            _errno, origin, icmp_type, icmp_code, _pad, _info, _data = struct.unpack("=IBBBBII", data[:16])
            if origin != _SO_EE_ORIGIN_ICMP:
                continue
            family, _port = struct.unpack("=HH", data[16:20])
            if family != socket.AF_INET:
                continue
            return icmp_type, icmp_code, socket.inet_ntop(socket.AF_INET, data[20:24])
    return None


class LinuxNetProbe:
    # -- ping --------------------------------------------------------------------------------------

    def ping_ipv4_host(
        self,
        host: str,
        *,
        count: int,
        timeout_ms: int,
        resolved_ip: str | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PingResult:
        sent = max(0, int(count))
        ip = resolved_ip
        resolve_error = None
        if not ip:
            from utils.net_resolve import resolve_ipv4

            ip = resolve_ipv4(host, timeout=max(1.0, float(timeout_ms) / 1000.0))
            resolve_error = "DNS_ERR"
        if not ip:
            return PingResult(ok=False, sent=sent, received=0, error_code=resolve_error, detail="DNS resolve failed")
        return self._ping(socket.AF_INET, ip, sent, timeout_ms, cancelled)

    def ping_ipv6(
        self, ip: str, *, count: int, timeout_ms: int, cancelled: Callable[[], bool] | None = None
    ) -> PingResult:
        return self._ping(socket.AF_INET6, ip.split("%", 1)[0], max(0, int(count)), timeout_ms, cancelled)

    def _ping(self, family: int, ip: str, sent: int, timeout_ms: int, cancelled) -> PingResult:
        sock = _open_ping_socket(family)
        if sock is None:
            return self._ping_with_program(family, ip, sent, timeout_ms)
        request_kind, reply_kind = (
            (_ICMP_ECHO_REQUEST, _ICMP_ECHO_REPLY) if family == socket.AF_INET else (_ICMP6_ECHO_REQUEST, _ICMP6_ECHO_REPLY)
        )
        rtts: list[float] = []
        ttl: int | None = None
        try:
            if family == socket.AF_INET:
                sock.setsockopt(socket.SOL_IP, _IP_RECVTTL, 1)
            for attempt in range(sent):
                if cancelled is not None and cancelled():
                    sent = attempt
                    break
                started = time.perf_counter()
                try:
                    sock.sendto(_echo_packet(request_kind, attempt + 1), (ip, 0))
                except OSError as exc:
                    return PingResult(
                        ok=False, sent=sent, received=0, resolved_ip=ip, error_code="SEND_FAILED", detail=str(exc)
                    )
                deadline = started + max(1, int(timeout_ms)) / 1000.0
                while True:
                    left = deadline - time.perf_counter()
                    if left <= 0:
                        break
                    ready, _, _ = select.select([sock], [], [], left)
                    if not ready:
                        break
                    try:
                        data, ancillary, _flags, _addr = sock.recvmsg(2048, 256)
                    except OSError:
                        break
                    if len(data) >= 8 and data[0] == reply_kind and struct.unpack("!H", data[6:8])[0] == attempt + 1:
                        rtts.append((time.perf_counter() - started) * 1000.0)
                        ttl = _received_ttl(ancillary) or ttl
                        break
        finally:
            sock.close()
        return _summarize(rtts, sent, ip, ttl)

    def _ping_with_program(self, family: int, ip: str, sent: int, timeout_ms: int) -> PingResult:
        """The ICMP socket is not allowed for this user: let the system's ``ping`` do it."""
        program = shutil.which("ping")
        if not program or sent <= 0:
            return PingResult(
                ok=False,
                sent=sent,
                received=0,
                resolved_ip=ip,
                error_code="UNSUPPORTED",
                detail="ICMP is not allowed (net.ipv4.ping_group_range) and there is no ping program",
            )
        wait = max(1, int(round(timeout_ms / 1000.0)))
        command = [program, "-n", "-c", str(sent), "-W", str(wait), ip]
        if family == socket.AF_INET6:
            command.insert(1, "-6")
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=sent * (wait + 1) + 2)
        except (OSError, subprocess.SubprocessError) as exc:
            return PingResult(ok=False, sent=sent, received=0, resolved_ip=ip, error_code="UNSUPPORTED", detail=str(exc))
        rtts = [float(value) for value in re.findall(r"time[=<]([\d.]+) ?ms", done.stdout)]
        ttls = re.findall(r"ttl=(\d+)", done.stdout, re.IGNORECASE)
        return _summarize(rtts, sent, ip, int(ttls[-1]) if ttls else None)

    # -- trace -------------------------------------------------------------------------------------

    def trace_hop_ipv4(self, ip: str, ttl: int, *, timeout_ms: int) -> TraceHopResult:
        sock = _open_ping_socket(socket.AF_INET)
        if sock is None:
            return TraceHopResult(HOP_UNSUPPORTED)
        started = time.perf_counter()
        try:
            sock.setsockopt(socket.SOL_IP, socket.IP_TTL, max(1, min(int(ttl), 255)))
            sock.setsockopt(socket.SOL_IP, _IP_RECVERR, 1)
            sock.sendto(_echo_packet(_ICMP_ECHO_REQUEST, 1), (ip, 0))
            poller = select.poll()
            poller.register(sock, select.POLLIN | select.POLLERR)
            deadline = started + max(1, int(timeout_ms)) / 1000.0
            while True:
                left = deadline - time.perf_counter()
                if left <= 0:
                    return TraceHopResult(HOP_SILENT)
                events = poller.poll(left * 1000.0)
                if not events:
                    return TraceHopResult(HOP_SILENT)
                rtt = (time.perf_counter() - started) * 1000.0
                if events[0][1] & select.POLLERR:
                    try:
                        _data, ancillary, _flags, _addr = sock.recvmsg(2048, 512, socket.MSG_ERRQUEUE)
                    except OSError:
                        continue
                    error = parse_icmp_error(ancillary)
                    if error is None:
                        continue
                    icmp_type, _code, address = error
                    if icmp_type == _ICMP_TIME_EXCEEDED or address != ip:
                        return TraceHopResult(HOP_ROUTER, address, rtt)
                    return TraceHopResult(HOP_TARGET, address, rtt)
                try:
                    data, _anc, _flags, addr = sock.recvmsg(2048, 256)
                except OSError:
                    return TraceHopResult(HOP_SILENT)
                if data and data[0] == _ICMP_ECHO_REPLY:
                    return TraceHopResult(HOP_TARGET, addr[0], rtt)
        except OSError:
            return TraceHopResult(HOP_UNSUPPORTED)
        finally:
            sock.close()

    # -- DNS ---------------------------------------------------------------------------------------

    def system_dns_servers(self) -> tuple[str, ...]:
        """IPv4 DNS servers: the real upstream ones when the stub of systemd-resolved (127.0.0.53) is in front."""
        found = _read_nameservers("/etc/resolv.conf")
        if any(address.startswith("127.") for address in found):
            upstream = _read_nameservers("/run/systemd/resolve/resolv.conf")
            if upstream:
                return upstream
        return found

    def query_ipv4(
        self, name: str, *, timeout: float, cancelled: Callable[[], bool] | None = None
    ) -> DnsAnswer:
        from utils import dns_wire
        from utils.socket_cancel import SocketCancel

        started = time.perf_counter()
        host = str(name or "").strip().rstrip(".")
        if not host:
            return DnsAnswer(status=DNS_STATUS_NAME_ERROR, detail="пустое имя")
        servers = self.system_dns_servers()
        if not servers:
            return DnsAnswer(status=-1, detail="в системе не заданы DNS-серверы")

        cancel = SocketCancel()
        outcome: dict[str, object] = {}
        done = threading.Event()
        total = max(0.05, float(timeout))

        def work() -> None:
            try:
                last = None
                for server in servers:
                    left = total - (time.perf_counter() - started)
                    if left <= 0.05 or cancel.cancelled:
                        break
                    last = dns_wire.query_server(
                        server, host, dns_wire.TYPE_A, timeout_s=min(left, 2.0), attempts=1, cancel=cancel
                    )
                    if last.answered:
                        break
                outcome["result"] = last
            finally:
                done.set()

        threading.Thread(target=work, name="dns-query", daemon=True).start()
        deadline = started + total
        stop = ""
        while not done.wait(0.05):
            if cancelled is not None and cancelled():
                stop = "cancelled"
                break
            if time.perf_counter() >= deadline:
                stop = "timeout"
                break
        elapsed = (time.perf_counter() - started) * 1000.0
        if stop:
            cancel.cancel()
            code = ERROR_CANCELLED if stop == "cancelled" else ERROR_TIMEOUT
            return DnsAnswer(status=code, detail=status_text(code), elapsed_ms=elapsed)
        return _answer_from(outcome.get("result"), elapsed)

    # -- TCP, hosts --------------------------------------------------------------------------------

    def tcp_server_window(self, ip: str, *, port: int = 443, timeout: float = 4.0) -> int | None:
        try:
            with socket.create_connection((ip, int(port)), timeout=timeout) as sock:
                info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 512)
        except OSError:
            return None
        if len(info) < _TCPI_SND_WND_OFFSET + 4:
            return None  # the kernel is older than 6.2 and does not report the peer's window
        window = struct.unpack_from("=I", info, _TCPI_SND_WND_OFFSET)[0]
        return int(window) or None

    def hosts_file_path(self) -> str:
        return "/etc/hosts"


def _read_nameservers(path: str) -> tuple[str, ...]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
    except OSError:
        return ()
    found: list[str] = []
    for line in lines:
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0] == "nameserver" and parts[1] not in found:
            try:
                socket.inet_pton(socket.AF_INET, parts[1])
            except OSError:
                continue
            found.append(parts[1])
    return tuple(found)


def _summarize(rtts: list[float], sent: int, ip: str, ttl: int | None) -> PingResult:
    received = len(rtts)
    if received:
        average = sum(rtts) / received
        return PingResult(
            ok=True,
            average_ms=average,
            sent=sent,
            received=received,
            resolved_ip=ip,
            detail=f"{average:.0f}ms",
            min_ms=min(rtts),
            max_ms=max(rtts),
            ttl=ttl,
        )
    return PingResult(ok=False, sent=sent, received=0, resolved_ip=ip, error_code="TIMEOUT", detail="Timeout")


def _answer_from(result, elapsed_ms: float) -> DnsAnswer:
    """The answer of ``utils.dns_wire`` in the vocabulary of the diagnostics (Windows status numbers)."""
    from utils import dns_wire

    if result is None or not result.answered:
        code = ERROR_CANCELLED if result is not None and result.failure == dns_wire.FAILURE_CANCELLED else ERROR_TIMEOUT
        return DnsAnswer(status=code, detail=status_text(code), elapsed_ms=elapsed_ms)
    ips = result.values(dns_wire.TYPE_A)
    cnames = result.values(dns_wire.TYPE_CNAME)
    if result.rcode == dns_wire.RCODE_OK:
        status = ERROR_SUCCESS if ips else DNS_STATUS_NO_RECORDS
    elif result.rcode == dns_wire.RCODE_NXDOMAIN:
        status = DNS_STATUS_NAME_ERROR
    elif result.rcode == 5:
        status = DNS_STATUS_REFUSED
    else:
        status = DNS_STATUS_SERVER_FAILURE
    return DnsAnswer(
        ips=tuple(ips),
        cnames=tuple(cnames),
        status=status,
        detail="" if ips else status_text(status),
        elapsed_ms=elapsed_ms,
    )
