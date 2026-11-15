"""Small privileged jobs besides the engine: DNS servers of one interface, the DNS cache and the hosts file.

Every value is checked here, in the root process, no matter what the window already checked: a member of the group
``zapretgui`` may ask for these things (the group is trusted like ``docker``), but only for exactly these things and
only in this shape. Nothing here runs a shell.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from platforms.linux.helper.protocol import answer_error, answer_ok

SYS_CLASS_NET = "/sys/class/net"
HOSTS_PATH = "/etc/hosts"
HOSTS_BACKUP = "/var/lib/zapretgui/hosts.original"
MAX_DNS_SERVERS = 8
MAX_HOSTS_BYTES = 1 << 20
OPERATIONS = frozenset({"dns_set", "dns_flush", "hosts_write"})
INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")


def run_command(command: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, (done.stderr or done.stdout).strip()


@dataclass
class SystemOps:
    run: Callable[[list[str]], tuple[int, str]] = run_command
    net_class: str = SYS_CLASS_NET
    hosts_path: str = HOSTS_PATH
    hosts_backup: str = HOSTS_BACKUP

    def handle(self, op: str, request: dict) -> dict:
        if op == "dns_set":
            return self.dns_set(request.get("interface"), request.get("servers"))
        if op == "dns_flush":
            return self.dns_flush()
        if op == "hosts_write":
            return self.hosts_write(request.get("content"))
        return answer_error(f"unknown operation {op!r}")

    # -- DNS ----------------------------------------------------------------------------------------

    def dns_set(self, interface, servers) -> dict:
        name = str(interface or "")
        if not INTERFACE_NAME.match(name) or not os.path.isdir(os.path.join(self.net_class, name)):
            return answer_error(f"not a network interface: {name!r}")
        if not isinstance(servers, list) or len(servers) > MAX_DNS_SERVERS:
            return answer_error("servers must be a list of at most %d addresses" % MAX_DNS_SERVERS)
        addresses: list[str] = []
        for item in servers:
            try:
                address = ipaddress.ip_address(str(item).strip())
            except ValueError:
                return answer_error(f"not an IP address: {str(item)[:60]!r}")
            if address.is_multicast or address.is_unspecified:
                return answer_error(f"not a usable DNS server address: {address}")
            if str(address) not in addresses:
                addresses.append(str(address))
        command = ["resolvectl", "dns", name, *addresses] if addresses else ["resolvectl", "revert", name]
        code, text = self.run(command)
        if code != 0:
            return answer_error(f"resolvectl failed: {text or code}")
        return answer_ok(interface=name, servers=addresses)

    def dns_flush(self) -> dict:
        code, text = self.run(["resolvectl", "flush-caches"])
        if code != 0:
            return answer_error(f"resolvectl failed: {text or code}")
        return answer_ok()

    # -- hosts --------------------------------------------------------------------------------------

    def hosts_write(self, content) -> dict:
        if not isinstance(content, str):
            return answer_error("content must be text")
        data = content.encode("utf-8")
        if len(data) > MAX_HOSTS_BYTES:
            return answer_error("the hosts file is too large")
        if "\0" in content:
            return answer_error("the hosts file must be text")
        if not _has_localhost(content):
            return answer_error("the hosts file must keep the localhost line (127.0.0.1 localhost)")
        target = Path(self.hosts_path)
        try:
            previous = target.read_bytes() if target.exists() else None
            backup = Path(self.hosts_backup)
            if previous is not None and not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(previous)  # the very first original stays for good
            with open(target, "wb") as handle:  # in place: the unit allows exactly this file
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(target, 0o644)
        except OSError as exc:
            return answer_error(f"cannot write {self.hosts_path}: {exc}")
        return answer_ok(size=len(data))


def _has_localhost(content: str) -> bool:
    for line in content.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and fields[0] in ("127.0.0.1", "::1", "127.0.1.1") and "localhost" in fields[1:]:
            return True
    return False
