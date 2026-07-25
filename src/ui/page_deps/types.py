from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class DnsPageDeps:
    dns_feature: object


@dataclass(frozen=True, slots=True)
class HostsPageDeps:
    hosts_feature: object


@dataclass(frozen=True, slots=True)
class PremiumPageDeps:
    premium_feature: object
    subscription_state_store: object


@dataclass(frozen=True, slots=True)
class DpiRuntimeActions:
    handle_launch_method_changed: Callable[..., object]


@dataclass(frozen=True, slots=True)
class UpdateRuntimeActions:
    is_any_running: Callable[..., bool]
    shutdown_sync: Callable[..., object]
    is_available: Callable[..., bool]
    restart: Callable[..., object]
    mark_stopped: Callable[..., object]
    request_exit: Callable[..., object]


__all__ = [
    "DpiRuntimeActions",
    "DnsPageDeps",
    "HostsPageDeps",
    "PremiumPageDeps",
    "UpdateRuntimeActions",
]
