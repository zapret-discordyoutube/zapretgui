"""Linux DNS backend and the helper's system operations, against fake system calls."""
from __future__ import annotations

import json
import os

import pytest

from platforms.dns_types import DnsBackendError, StaticDns
from platforms.linux.dns import LinuxDns
from platforms.linux.helper import system_ops
from platforms.linux.helper.client import HelperUnavailable

pytestmark = pytest.mark.real_dns


class FakeSystem:
    """Answers commands from a table; remembers every command that was run."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, command):
        self.calls.append(list(command))
        for prefix, answer in self.answers:
            if list(command[: len(prefix)]) == list(prefix):
                return answer
        return 127, "not found"


class FakeHelper:
    def __init__(self, ok=True, unavailable=False):
        self.ok, self.unavailable, self.sets, self.flushes = ok, unavailable, [], 0

    def dns_set(self, interface, servers):
        if self.unavailable:
            raise HelperUnavailable("helper is not running")
        self.sets.append((interface, list(servers)))
        return {"ok": self.ok, "error": "refused"}

    def dns_flush(self):
        if self.unavailable:
            raise HelperUnavailable("helper is not running")
        self.flushes += 1
        return {"ok": True}


def make_net(tmp_path):
    """/sys/class/net with a wired card, a Wi-Fi card, a docker bridge and lo."""
    def card(name, index, kind, state="up", device=False, wireless=False, virtual=False):
        base = tmp_path / "devices" / ("virtual" if virtual else "pci") / name
        base.mkdir(parents=True)
        (base / "ifindex").write_text(str(index))
        (base / "type").write_text(str(kind))
        (base / "operstate").write_text(state)
        if device:
            (base / "device").mkdir()
        if wireless:
            (base / "wireless").mkdir()
        os.symlink(base, tmp_path / "net" / name)

    (tmp_path / "net").mkdir()
    card("lo", 1, 772, state="unknown", virtual=True)
    card("enp3s0", 2, 1, device=True)
    card("wlp2s0", 3, 1, state="down", device=True, wireless=True)
    card("docker0", 4, 1, virtual=True)
    card("eth0", 5, 1, virtual=True)  # the only card of a container: virtual, but it is the network
    card("veth1a2b", 6, 1, virtual=True)
    return str(tmp_path / "net")


def test_interfaces_get_kinds_flags_and_the_dns_they_use(tmp_path):
    system = FakeSystem([
        (["resolvectl", "dns", "enp3s0"], (0, "Link 2 (enp3s0): 192.168.1.1 fe80::1")),
        (["resolvectl", "dns"], (0, "")),
    ])
    dns = LinuxDns(system, net_class=make_net(tmp_path))
    by_name = {item.name: item for item in dns.list_interfaces()}
    assert set(by_name) == {"enp3s0", "wlp2s0", "docker0", "eth0", "veth1a2b"}  # lo is never offered
    wired, wifi, bridge = by_name["enp3s0"], by_name["wlp2s0"], by_name["docker0"]
    assert (wired.if_type, wifi.if_type, bridge.if_type) == (6, 71, 6)
    assert wired.hardware and wired.connector and wired.connected and not wired.filter
    assert not wifi.connected and wifi.hardware
    assert bridge.filter and not bridge.hardware and by_name["veth1a2b"].filter  # bridges and container ends are never offered
    assert not by_name["eth0"].filter  # a virtual card that is the machine's network stays
    assert wired.dns_ipv4 == ("192.168.1.1",) and wired.dns_ipv6 == ("fe80::1",)


def test_internet_route_is_the_interface_of_the_default_route(tmp_path):
    net = make_net(tmp_path)
    system = FakeSystem([
        (["ip", "-j", "route", "get", "1.1.1.1"], (0, json.dumps([{"dst": "1.1.1.1", "dev": "enp3s0"}]))),
        (["ip", "-j", "route", "get", "2606:4700:4700::1111"], (2, "RTNETLINK answers: Network is unreachable")),
    ])
    route = LinuxDns(system, net_class=net).internet_route()
    assert route.ipv4_index == 2 and route.ipv6_index == 0 and not route.has_ipv6


def test_network_manager_reads_the_manual_servers_of_the_connection(tmp_path):
    system = FakeSystem([
        (["nmcli", "-t", "-f", "RUNNING", "general"], (0, "running")),
        (["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "enp3s0"], (0, "uuid-1\n")),
        (["nmcli", "-g", "ipv4.dns,ipv6.dns", "connection", "show", "uuid-1"], (0, "1.1.1.1,1.0.0.1\n2606:4700:4700::1111\n")),
    ])
    static = LinuxDns(system, net_class=make_net(tmp_path)).read_static_dns("enp3s0")
    assert static == StaticDns(ipv4=("1.1.1.1", "1.0.0.1"), ipv6=("2606:4700:4700::1111",))


def test_network_manager_write_sets_servers_ignores_auto_and_reapplies(tmp_path):
    system = FakeSystem([
        (["nmcli", "-t", "-f", "RUNNING", "general"], (0, "running")),
        (["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "enp3s0"], (0, "uuid-1")),
        (["nmcli", "connection", "modify"], (0, "")),
        (["nmcli", "device", "reapply"], (0, "")),
    ])
    dns = LinuxDns(system, net_class=make_net(tmp_path))
    dns.write_dns("enp3s0", ["1.1.1.1", "1.0.0.1", "1.1.1.1"], ipv6=False)
    dns.write_dns("enp3s0", [], ipv6=True)
    modify = [c for c in system.calls if c[:3] == ["nmcli", "connection", "modify"]]
    assert modify[0] == ["nmcli", "connection", "modify", "uuid-1", "ipv4.dns", "1.1.1.1,1.0.0.1", "ipv4.ignore-auto-dns", "yes"]
    assert modify[1] == ["nmcli", "connection", "modify", "uuid-1", "ipv6.dns", "", "ipv6.ignore-auto-dns", "no"]  # empty: automatic again
    assert ["nmcli", "device", "reapply", "enp3s0"] in system.calls


def test_network_manager_refusal_becomes_a_readable_error(tmp_path):
    system = FakeSystem([
        (["nmcli", "-t", "-f", "RUNNING", "general"], (0, "running")),
        (["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "enp3s0"], (0, "uuid-1")),
        (["nmcli", "connection", "modify"], (4, "Error: Insufficient privileges")),
    ])
    with pytest.raises(DnsBackendError, match="Insufficient privileges"):
        LinuxDns(system, net_class=make_net(tmp_path)).write_dns("enp3s0", ["1.1.1.1"], ipv6=False)


def test_a_card_networkmanager_leaves_alone_goes_through_resolved_even_while_it_runs(tmp_path):
    """Found on a real Debian 13 KDE machine: NetworkManager runs, but the wired card is "unmanaged" (it has no
    connection), so the servers must be set through systemd-resolved instead of failing with "connect first"."""
    helper = FakeHelper()
    system = FakeSystem([
        (["nmcli", "-t", "-f", "RUNNING", "general"], (0, "running")),
        (["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", "enp3s0"], (0, "")),
        (["resolvectl", "dns", "enp3s0"], (0, "Link 2 (enp3s0): 9.9.9.9")),
    ])
    dns = LinuxDns(system, net_class=make_net(tmp_path), helper=helper, record_path=tmp_path / "rec.json")
    dns.write_dns("enp3s0", ["9.9.9.9"], ipv6=False)
    assert helper.sets == [("enp3s0", ["9.9.9.9"])]
    assert not [c for c in system.calls if c[:2] == ["nmcli", "connection"]]
    assert dns.read_static_dns("enp3s0").ipv4 == ("9.9.9.9",)


def test_resolved_alone_goes_through_the_helper_and_keeps_both_families(tmp_path):
    helper = FakeHelper()
    system = FakeSystem([(["resolvectl", "dns", "enp3s0"], (0, "Link 2 (enp3s0): 1.1.1.1 2606:4700:4700::1111"))])
    dns = LinuxDns(system, net_class=make_net(tmp_path), helper=helper, record_path=tmp_path / "rec.json")
    dns.write_dns("enp3s0", ["1.1.1.1"], ipv6=False)
    dns.write_dns("enp3s0", ["2606:4700:4700::1111"], ipv6=True)
    assert helper.sets == [("enp3s0", ["1.1.1.1"]), ("enp3s0", ["1.1.1.1", "2606:4700:4700::1111"])]
    static = dns.read_static_dns("enp3s0")
    assert static.ipv4 == ("1.1.1.1",) and static.ipv6 == ("2606:4700:4700::1111",)
    dns.write_dns("enp3s0", [], ipv6=True)
    dns.write_dns("enp3s0", [], ipv6=False)
    assert helper.sets[-1] == ("enp3s0", [])  # nothing left: the helper reverts the link
    assert dns.read_static_dns("enp3s0") == StaticDns()


def test_a_manual_record_is_dropped_when_the_link_no_longer_has_those_servers(tmp_path):
    helper = FakeHelper()
    live = {"text": "Link 2 (enp3s0): 8.8.8.8"}
    system = lambda command: (0, live["text"]) if command[:2] == ["resolvectl", "dns"] else (127, "")
    dns = LinuxDns(system, net_class=make_net(tmp_path), helper=helper, record_path=tmp_path / "rec.json")
    dns.write_dns("enp3s0", ["1.1.1.1"], ipv6=False)
    assert dns.read_static_dns("enp3s0") == StaticDns()  # the link was reset by the network: the record is stale


def test_resolved_without_the_helper_says_what_to_do(tmp_path):
    dns = LinuxDns(FakeSystem([]), net_class=make_net(tmp_path), helper=FakeHelper(unavailable=True), record_path=tmp_path / "r.json")
    with pytest.raises(DnsBackendError, match="helper"):
        dns.write_dns("enp3s0", ["1.1.1.1"], ipv6=False)


def test_flush_tries_the_user_first_then_the_helper(tmp_path):
    helper = FakeHelper()
    assert LinuxDns(FakeSystem([(["resolvectl", "flush-caches"], (0, ""))]), helper=helper).flush_resolver_cache()
    assert helper.flushes == 0
    assert LinuxDns(FakeSystem([(["resolvectl", "flush-caches"], (1, "denied"))]), helper=helper).flush_resolver_cache()
    assert helper.flushes == 1
    assert not LinuxDns(FakeSystem([]), helper=FakeHelper(unavailable=True)).flush_resolver_cache()


def test_doh_is_not_claimed():
    assert LinuxDns(FakeSystem([])).is_doh_supported() is False


# -- the helper's side --------------------------------------------------------------------------------------


def ops(tmp_path, answers=None):
    net = tmp_path / "net"
    (net / "enp3s0").mkdir(parents=True)
    system = FakeSystem(answers or [(["resolvectl"], (0, ""))])
    return system_ops.SystemOps(run=system, net_class=str(net), hosts_path=str(tmp_path / "hosts"), hosts_backup=str(tmp_path / "state" / "hosts.original")), system


def test_dns_set_runs_resolvectl_with_checked_addresses(tmp_path):
    service, system = ops(tmp_path)
    answer = service.handle("dns_set", {"interface": "enp3s0", "servers": ["1.1.1.1", "2606:4700:4700::1111", "1.1.1.1"]})
    assert answer == {"ok": True, "interface": "enp3s0", "servers": ["1.1.1.1", "2606:4700:4700::1111"]}
    assert system.calls == [["resolvectl", "dns", "enp3s0", "1.1.1.1", "2606:4700:4700::1111"]]
    service.handle("dns_set", {"interface": "enp3s0", "servers": []})
    assert system.calls[-1] == ["resolvectl", "revert", "enp3s0"]


@pytest.mark.parametrize(
    "request_fields",
    [
        {"interface": "../etc", "servers": ["1.1.1.1"]},
        {"interface": "nope0", "servers": ["1.1.1.1"]},
        {"interface": "enp3s0 --help", "servers": ["1.1.1.1"]},
        {"interface": "enp3s0", "servers": ["1.1.1.1; reboot"]},
        {"interface": "enp3s0", "servers": ["-h"]},
        {"interface": "enp3s0", "servers": ["224.0.0.1"]},
        {"interface": "enp3s0", "servers": "1.1.1.1"},
        {"interface": "enp3s0", "servers": ["1.1.1.%d" % i for i in range(20)]},
    ],
)
def test_dns_set_refuses_anything_unusual(tmp_path, request_fields):
    service, system = ops(tmp_path)
    answer = service.handle("dns_set", request_fields)
    assert answer["ok"] is False and system.calls == []


def test_dns_failure_text_travels_back(tmp_path):
    service, _ = ops(tmp_path, [(["resolvectl"], (1, "Failed to set DNS: Access denied"))])
    assert "Access denied" in service.handle("dns_flush", {})["error"]


def test_hosts_write_keeps_a_backup_of_the_first_original_only(tmp_path):
    service, _ = ops(tmp_path)
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n# original\n", encoding="utf-8")
    assert service.handle("hosts_write", {"content": "127.0.0.1 localhost\n1.2.3.4 example.test\n"})["ok"]
    assert service.handle("hosts_write", {"content": "127.0.0.1 localhost\n5.6.7.8 other.test\n"})["ok"]
    assert hosts.read_text(encoding="utf-8").endswith("5.6.7.8 other.test\n")
    assert (tmp_path / "state" / "hosts.original").read_text(encoding="utf-8") == "127.0.0.1 localhost\n# original\n"
    assert oct(hosts.stat().st_mode & 0o777) == "0o644"


@pytest.mark.parametrize("content", [None, 5, "1.2.3.4 example.test\n", "127.0.0.1 localhost\n\0", "# 127.0.0.1 localhost\n", "127.0.0.1 localhost\n" + "x" * (1 << 21)])
def test_hosts_write_refuses_a_file_that_would_break_the_system(tmp_path, content):
    service, _ = ops(tmp_path)
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    assert service.handle("hosts_write", {"content": content})["ok"] is False
    assert hosts.read_text(encoding="utf-8") == "127.0.0.1 localhost\n"


def test_the_service_hands_these_operations_to_the_ops(tmp_path):
    from platforms.linux.helper.service import Caller, HelperService

    service_ops, system = ops(tmp_path)
    service = HelperService(ops=service_ops)
    answer = service.handle({"op": "dns_flush"}, Caller(uid=1000, home="/home/x", gid=1000))
    assert answer["ok"] is True and system.calls == [["resolvectl", "flush-caches"]]


def test_the_protocol_lists_the_new_operations():
    from platforms.linux.helper.protocol import OPERATIONS

    assert {"dns_set", "dns_flush", "hosts_write"} <= OPERATIONS
