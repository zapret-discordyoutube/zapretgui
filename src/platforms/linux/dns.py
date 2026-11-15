"""DNS of the network interfaces on Linux.

Two systems own the DNS servers of a desktop, and the program asks the one that really does:

- **NetworkManager** (nearly every desktop): the servers are a setting of the *connection*. They are read and changed
  with ``nmcli`` as the normal user (NetworkManager asks polkit; a person at the screen is allowed) and stay after a
  reboot. This is the same as typing the DNS into the network settings window.
- **systemd-resolved alone** (servers, minimal installs): the servers are set on the *link* through the root helper
  (``resolvectl dns``). They last until the link goes down; the program remembers what it set to show it as manual.

Without either the page shows the interfaces but changing is refused with a text saying what is missing.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from platforms.dns_types import (
    IF_TYPE_ETHERNET_CSMACD,
    IF_TYPE_IEEE80211,
    IF_TYPE_SOFTWARE_LOOPBACK,
    DnsBackendError,
    InternetRoute,
    RawInterface,
    StaticDns,
    split_name_servers,
)

SYS_CLASS_NET = "/sys/class/net"
PROBE_V4 = "1.1.1.1"
PROBE_V6 = "2606:4700:4700::1111"
ARPHRD_ETHER = 1
ARPHRD_LOOPBACK = 772
# Interfaces of containers, virtual machines and bridges: never offered as "the network of this computer".
# (A virtual card that is simply the machine's only network, as in a container, is offered.)
SERVICE_INTERFACE_PREFIXES = ("docker", "br-", "veth", "virbr", "vnet", "cni", "flannel", "cali", "lxcbr", "lxdbr", "podman", "tailscale")
OTHER_TYPE = 0  # not Ethernet, Wi-Fi or loopback: the page ignores it


def run_command(command: Sequence[str]) -> tuple[int, str]:
    """``(exit code, stdout)``; 127 when the program is missing. Never raises."""
    try:
        done = subprocess.run(list(command), capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout if done.returncode == 0 else (done.stderr or done.stdout)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _is_ipv6(address: str) -> bool:
    return ":" in address


class LinuxDns:
    def __init__(
        self,
        run: Callable[[Sequence[str]], tuple[int, str]] = run_command,
        *,
        net_class: str = SYS_CLASS_NET,
        helper=None,
        record_path: str | os.PathLike | None = None,
    ) -> None:
        self._run = run
        self._net = net_class
        self._helper = helper
        self._record_path = Path(record_path) if record_path else None

    # -- the helper (root jobs) and the record of what this program set -------------------------------------

    def _helper_client(self):
        if self._helper is None:
            from platforms.linux.helper.client import HelperClient

            self._helper = HelperClient()
        return self._helper

    def _record_file(self) -> Path:
        if self._record_path is not None:
            return self._record_path
        from config.runtime_layout import APPLICATION_PATHS

        return Path(APPLICATION_PATHS.settings_database).parent / "dns_manual.json"

    def _load_record(self) -> dict:
        try:
            data = json.loads(self._record_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_record(self, record: dict) -> None:
        path = self._record_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    # -- which system owns the DNS -------------------------------------------------------------------------

    def uses_network_manager(self) -> bool:
        code, text = self._run(["nmcli", "-t", "-f", "RUNNING", "general"])
        return code == 0 and text.strip() == "running"

    # -- interfaces ----------------------------------------------------------------------------------------

    def _interface_names(self) -> list[str]:
        try:
            return sorted(os.listdir(self._net))
        except OSError:
            return []

    def _interface_type(self, name: str) -> int:
        base = os.path.join(self._net, name)
        if os.path.isdir(os.path.join(base, "wireless")) or os.path.isdir(os.path.join(base, "phy80211")):
            return IF_TYPE_IEEE80211
        kind = _read(os.path.join(base, "type"))
        if kind == str(ARPHRD_LOOPBACK):
            return IF_TYPE_SOFTWARE_LOOPBACK
        if kind == str(ARPHRD_ETHER):
            return IF_TYPE_ETHERNET_CSMACD
        return OTHER_TYPE

    def _live_dns(self, name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The servers the interface really uses now (from the DHCP or what was set): systemd-resolved, else NetworkManager."""
        code, text = self._run(["resolvectl", "dns", name])
        servers: tuple[str, ...] = ()
        if code == 0 and ":" in text:
            servers = split_name_servers(text.split(":", 1)[1].replace("\n", " "))
        if not servers:
            code, text = self._run(["nmcli", "-g", "IP4.DNS,IP6.DNS", "device", "show", name])
            if code == 0:
                servers = split_name_servers(text.replace("\n", " ").replace("|", " "))
        return (
            tuple(item for item in servers if not _is_ipv6(item)),
            tuple(item for item in servers if _is_ipv6(item)),
        )

    def list_interfaces(self) -> list[RawInterface]:
        result: list[RawInterface] = []
        for name in self._interface_names():
            if name == "lo":
                continue
            base = os.path.join(self._net, name)
            try:
                index = int(_read(os.path.join(base, "ifindex")) or 0)
            except ValueError:
                index = 0
            if not index:
                continue
            hardware = os.path.exists(os.path.join(base, "device"))
            service_interface = name.startswith(SERVICE_INTERFACE_PREFIXES)
            v4, v6 = self._live_dns(name)
            result.append(
                RawInterface(
                    guid=name,
                    index=index,
                    ipv6_index=index,
                    name=name,
                    description=_read(os.path.join(base, "device", "uevent")) and _device_title(base) or name,
                    if_type=self._interface_type(name),
                    connected=_read(os.path.join(base, "operstate")) in ("up", "unknown") and _read(os.path.join(base, "carrier")) != "0",
                    hardware=hardware,
                    filter=service_interface,
                    connector=hardware,
                    dns_ipv4=v4,
                    dns_ipv6=v6,
                )
            )
        return result

    def _route_index(self, probe: str) -> int:
        code, text = self._run(["ip", "-j", "route", "get", probe])
        if code != 0:
            return 0
        try:
            device = str(json.loads(text)[0].get("dev") or "")
        except (ValueError, IndexError, AttributeError, TypeError):
            return 0
        try:
            return int(_read(os.path.join(self._net, device, "ifindex")) or 0) if device else 0
        except ValueError:
            return 0

    def internet_route(self) -> InternetRoute:
        return InternetRoute(ipv4_index=self._route_index(PROBE_V4), ipv6_index=self._route_index(PROBE_V6))

    # -- the servers set by hand ---------------------------------------------------------------------------

    def _connection_uuid(self, name: str) -> str:
        code, text = self._run(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", name])
        return text.strip() if code == 0 else ""

    def read_static_dns(self, guid: str) -> StaticDns:
        if self.uses_network_manager():
            uuid = self._connection_uuid(guid)
            if not uuid:
                return StaticDns()
            code, text = self._run(["nmcli", "-g", "ipv4.dns,ipv6.dns", "connection", "show", uuid])
            if code != 0:
                raise DnsBackendError(f"nmcli: {text.strip() or code}")
            lines = (text.split("\n") + ["", ""])[:2]
            return StaticDns(ipv4=split_name_servers(lines[0]), ipv6=split_name_servers(lines[1]))
        # systemd-resolved alone: what this program set stays "manual" while the link still has exactly it
        entry = self._load_record().get(guid) or {}
        live_v4, live_v6 = self._live_dns(guid)
        ipv4 = tuple(entry.get("ipv4") or ())
        ipv6 = tuple(entry.get("ipv6") or ())
        return StaticDns(ipv4=ipv4 if ipv4 and set(ipv4) <= set(live_v4) else (), ipv6=ipv6 if ipv6 and set(ipv6) <= set(live_v6) else ())

    def write_dns(self, guid: str, servers: Sequence[str], *, ipv6: bool, doh_templates=None) -> None:
        wanted = [item for item in dict.fromkeys(str(s).strip() for s in servers) if item]
        if self.uses_network_manager():
            self._write_network_manager(guid, wanted, ipv6=ipv6)
        else:
            self._write_resolved(guid, wanted, ipv6=ipv6)

    def _write_network_manager(self, name: str, servers: list[str], *, ipv6: bool) -> None:
        uuid = self._connection_uuid(name)
        if not uuid:
            raise DnsBackendError(f"NetworkManager has no active connection on {name}: connect first.")
        family = "ipv6" if ipv6 else "ipv4"
        code, text = self._run(
            ["nmcli", "connection", "modify", uuid, f"{family}.dns", ",".join(servers), f"{family}.ignore-auto-dns", "yes" if servers else "no"]
        )
        if code != 0:
            raise DnsBackendError(f"NetworkManager refused the change: {text.strip() or code}")
        code, text = self._run(["nmcli", "device", "reapply", name])
        if code != 0:
            raise DnsBackendError(f"The setting is saved, but NetworkManager could not apply it now: {text.strip() or code}")

    def _write_resolved(self, name: str, servers: list[str], *, ipv6: bool) -> None:
        from platforms.linux.helper.client import HelperUnavailable

        record = self._load_record()
        entry = dict(record.get(name) or {})
        entry["ipv6" if ipv6 else "ipv4"] = servers
        everything = [*entry.get("ipv4", []), *entry.get("ipv6", [])]
        try:
            answer = self._helper_client().dns_set(name, everything)
        except HelperUnavailable as exc:
            raise DnsBackendError(str(exc)) from exc
        if not answer.get("ok"):
            raise DnsBackendError(str(answer.get("error") or "the helper refused the change"))
        if everything:
            record[name] = entry
        else:
            record.pop(name, None)
        self._save_record(record)

    # -- cache and encryption ------------------------------------------------------------------------------

    def flush_resolver_cache(self) -> bool:
        code, _text = self._run(["resolvectl", "flush-caches"])
        if code == 0:
            return True
        from platforms.linux.helper.client import HelperUnavailable

        try:
            return bool(self._helper_client().dns_flush().get("ok"))
        except HelperUnavailable:
            return False

    def is_doh_supported(self) -> bool:
        return False  # systemd-resolved speaks DNS over TLS, not HTTPS; the program then writes plain servers


def _device_title(base: str) -> str:
    """A readable name for the interface: the driver name from the uevent, else the interface name."""
    for line in _read(os.path.join(base, "device", "uevent")).splitlines():
        if line.startswith("DRIVER="):
            return f"{os.path.basename(base)} ({line.split('=', 1)[1]})"
    return os.path.basename(base)
