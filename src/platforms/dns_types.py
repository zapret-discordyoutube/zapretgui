"""What the DNS settings code knows about a network interface, whatever the system calls it.

``guid`` is the system's own name for the interface and is only handed back to the same backend: a GUID string on
Windows, the interface name (``enp3s0``) on Linux. ``if_type`` uses the Windows numbers (6 Ethernet, 71 Wi-Fi,
24 loopback): every backend maps its own kinds to them, so the choice of "which adapters are ours" stays in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_IEEE80211 = 71
IF_OPER_STATUS_UP = 1


class DnsBackendError(OSError):
    """The system refused to read or change DNS; the text is already readable for a person."""


@dataclass(frozen=True, slots=True)
class RawInterface:
    """A network interface as the system describes it."""

    guid: str
    index: int
    ipv6_index: int
    name: str
    description: str
    if_type: int
    connected: bool
    hardware: bool = False
    filter: bool = False
    connector: bool = False
    dns_ipv4: tuple[str, ...] = ()
    dns_ipv6: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InternetRoute:
    """Indexes of the interfaces the internet goes through (0: no route)."""

    ipv4_index: int = 0
    ipv6_index: int = 0

    @property
    def has_ipv6(self) -> bool:
        return self.ipv6_index != 0


@dataclass(frozen=True, slots=True)
class StaticDns:
    """DNS set by hand; empty means the interface gets its DNS automatically."""

    ipv4: tuple[str, ...] = field(default_factory=tuple)
    ipv6: tuple[str, ...] = field(default_factory=tuple)


def split_name_servers(value: str | None) -> tuple[str, ...]:
    """"1.1.1.1,1.0.0.1" / "a b" / "a;b" -> a tuple of addresses without repeats."""
    text = str(value or "").replace(";", " ").replace(",", " ")
    return tuple(dict.fromkeys(part for part in text.split() if part))
