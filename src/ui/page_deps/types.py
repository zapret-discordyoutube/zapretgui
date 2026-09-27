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
class FakesPageDeps:
    """Узкие действия страницы «Фейки»: только фабрики фоновых worker-ов и навигация."""

    create_snapshot_worker: Callable[..., object]
    create_import_worker: Callable[..., object]
    create_delete_worker: Callable[..., object]
    create_open_folder_worker: Callable[..., object]
    open_control_page: Callable[[], object]


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
    "FakesPageDeps",
    "HostsPageDeps",
    "PremiumPageDeps",
    "UpdateRuntimeActions",
]
