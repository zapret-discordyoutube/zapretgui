from __future__ import annotations

import re
from dataclasses import dataclass


_DNS_PROFILE_IP_SUFFIX = re.compile(r"\s*\(\s*(?:\d{1,3}\.){3}\d{1,3}\s*\)\s*$")


@dataclass(slots=True)
class HostsSelectionSyncEntry:
    service_name: str
    direct_only: bool
    available_profiles: list[str]
    selected_profile: str | None
    has_active_domains: bool
    toggle_enabled: bool
    toggle_checked: bool


@dataclass(slots=True)
class HostsSelectionSyncPlan:
    entries: dict[str, HostsSelectionSyncEntry]
    new_selection: dict[str, str]


@dataclass(slots=True)
class HostsServiceRowPlan:
    service_name: str
    icon_name: str
    icon_color: str | None
    direct_only: bool
    available_profiles: list[str]
    profile_items: list[tuple[str, str]]
    selected_profile: str | None
    toggle_enabled: bool
    toggle_checked: bool
    unavailable_reason: str | None = None


@dataclass(slots=True)
class HostsServiceGroupPlan:
    title: str
    direct_only: bool
    service_names: list[str]
    common_profiles: list[tuple[str, str]]
    rows: list[HostsServiceRowPlan]


@dataclass(slots=True)
class HostsServicesCatalogPlan:
    groups: list[HostsServiceGroupPlan]
    new_selection: dict[str, str]
    selection_changed: bool


def _iter_ip_values(raw_ips) -> list[str]:
    if isinstance(raw_ips, str):
        values = [raw_ips]
    elif isinstance(raw_ips, (list, tuple, set, frozenset)):
        values = list(raw_ips)
    else:
        values = [raw_ips]
    return [str(ip or "").strip() for ip in values if str(ip or "").strip()]


def _normalize_active_domains_map(active_domains_map: dict[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for domain, ip in (active_domains_map or {}).items():
        domain_key = str(domain or "").strip().casefold()
        if not domain_key or domain_key in normalized:
            continue
        ip_values = _iter_ip_values(ip)
        if not ip_values:
            continue
        normalized[domain_key] = ip_values[0]
    return normalized


def _normalize_active_domain_ip_sets(active_domains_map: dict[str, object]) -> dict[str, set[str]]:
    normalized: dict[str, set[str]] = {}
    for domain, ips in (active_domains_map or {}).items():
        domain_key = str(domain or "").strip().casefold()
        if not domain_key:
            continue
        values = normalized.setdefault(domain_key, set())
        for ip in _iter_ip_values(ips):
            values.add(ip.casefold())
    return {domain: ips for domain, ips in normalized.items() if ips}


def _domain_ip_sets_are_active(
    required_domain_ips: dict[str, set[str]],
    active_domain_ips: dict[str, set[str]],
) -> bool:
    if not required_domain_ips or not active_domain_ips:
        return False
    for domain_key, required_ips in required_domain_ips.items():
        active_ips = active_domain_ips.get(domain_key)
        if not active_ips or not required_ips <= active_ips:
            return False
    return True


def _domain_ip_sets_have_allowed_match(
    domain_ip_candidates: dict[str, set[str]],
    active_domain_ips: dict[str, set[str]],
) -> bool:
    if not domain_ip_candidates or not active_domain_ips:
        return False
    for domain_key, allowed_ips in domain_ip_candidates.items():
        active_ips = active_domain_ips.get(domain_key)
        if not active_ips or not allowed_ips or active_ips.isdisjoint(allowed_ips):
            return False
    return True


def _service_has_active_domains_from_index(
    service_name: str,
    normalized_active: dict[str, str],
    domain_names_by_service: dict[str, list[str]],
) -> bool:
    if not normalized_active:
        return False
    for domain in domain_names_by_service.get(service_name, []) or []:
        if str(domain or "").strip().casefold() in normalized_active:
            return True
    return False


def _infer_direct_toggle_from_index(
    service_name: str,
    direct_profile: str | None,
    active_domain_ips: dict[str, set[str]],
    profile_domain_ip_candidates_by_service: dict[str, dict[str, dict[str, list[str]]]],
) -> bool:
    if not active_domain_ips or not direct_profile:
        return False
    profile_candidates = profile_domain_ip_candidates_by_service.get(service_name, {}) or {}
    domain_ip_candidates = profile_candidates.get(direct_profile, {}) or {}
    if not domain_ip_candidates:
        return False

    required = {
        str(domain_key or "").strip().casefold(): {
            str(ip or "").strip().casefold()
            for ip in (allowed_ips or [])
            if str(ip or "").strip()
        }
        for domain_key, allowed_ips in domain_ip_candidates.items()
        if str(domain_key or "").strip()
    }
    required = {domain_key: ips for domain_key, ips in required.items() if ips}
    return _domain_ip_sets_have_allowed_match(required, active_domain_ips)


def _infer_profile_from_ip_candidates_index(
    service_name: str,
    available_profiles: list[str],
    active_domain_ips: dict[str, set[str]],
    profile_domain_ip_candidates_by_service: dict[str, dict[str, dict[str, list[str]]]],
) -> str | None:
    if not active_domain_ips or not available_profiles:
        return None

    profile_candidates = profile_domain_ip_candidates_by_service.get(service_name, {}) or {}
    for profile_name in available_profiles:
        domain_ip_candidates = profile_candidates.get(profile_name, {}) or {}
        if not domain_ip_candidates:
            continue
        required = {
            str(domain_key or "").strip().casefold(): {
                str(ip or "").strip().casefold()
                for ip in (allowed_ips or [])
                if str(ip or "").strip()
            }
            for domain_key, allowed_ips in domain_ip_candidates.items()
            if str(domain_key or "").strip()
        }
        required = {domain_key: ips for domain_key, ips in required.items() if ips}
        if _domain_ip_sets_are_active(required, active_domain_ips):
            return profile_name
    return None


def build_selection_sync_plan(
    *,
    service_names: list[str],
    active_domains_map: dict[str, object],
    available_profiles_by_service: dict[str, list[str]],
    service_has_proxy_by_service: dict[str, bool],
    direct_profile: str | None,
    domain_names_by_service: dict[str, list[str]],
    profile_domain_ip_candidates_by_service: dict[str, dict[str, dict[str, list[str]]]],
) -> HostsSelectionSyncPlan:
    """Что сейчас включено по строкам блока ZapretGUI (по готовому индексу каталога)."""
    direct_profile = direct_profile if isinstance(direct_profile, str) else None
    normalized_active = _normalize_active_domains_map(active_domains_map)
    active_domain_ips = _normalize_active_domain_ip_sets(active_domains_map)
    entries: dict[str, HostsSelectionSyncEntry] = {}
    new_selection: dict[str, str] = {}

    for service_name in service_names:
        direct_only = not bool(service_has_proxy_by_service.get(service_name))
        available = list(available_profiles_by_service.get(service_name) or [])
        selected_profile: str | None = None
        has_active_domains = _service_has_active_domains_from_index(
            service_name,
            normalized_active,
            domain_names_by_service,
        )
        toggle_enabled = False
        toggle_checked = False

        if direct_only:
            enabled = _infer_direct_toggle_from_index(
                service_name,
                direct_profile,
                active_domain_ips,
                profile_domain_ip_candidates_by_service,
            )
            toggle_enabled = bool(direct_profile and direct_profile in available)
            toggle_checked = bool(enabled and toggle_enabled)
            if toggle_checked and direct_profile:
                selected_profile = direct_profile
                new_selection[service_name] = direct_profile
        else:
            inferred = _infer_profile_from_ip_candidates_index(
                service_name,
                available,
                active_domain_ips,
                profile_domain_ip_candidates_by_service,
            )
            if inferred:
                selected_profile = inferred
                new_selection[service_name] = inferred

        entries[service_name] = HostsSelectionSyncEntry(
            service_name=service_name,
            direct_only=direct_only,
            available_profiles=available,
            selected_profile=selected_profile,
            has_active_domains=has_active_domains,
            toggle_enabled=toggle_enabled,
            toggle_checked=toggle_checked,
        )

    return HostsSelectionSyncPlan(entries=entries, new_selection=new_selection)


def format_dns_profile_label(profile_name: str) -> str:
    from hosts.proxy_domains import get_dns_profile_display_name

    label = (get_dns_profile_display_name(profile_name) or profile_name or "").strip()
    if not label:
        return ""
    return _DNS_PROFILE_IP_SUFFIX.sub("", label).strip()


IPV6_UNAVAILABLE_REASON = "Недоступно: для этого сервиса требуется IPv6-подключение"


def _service_is_ipv6_only(candidates_by_profile: dict[str, dict[str, list[str]]]) -> bool:
    from hosts.ipv6_detection import is_ipv6_address

    all_ips = [
        ip
        for domain_candidates in (candidates_by_profile or {}).values()
        for ips in (domain_candidates or {}).values()
        for ip in (ips or [])
    ]
    return bool(all_ips) and all(is_ipv6_address(ip) for ip in all_ips)


def build_services_catalog_plan(
    *,
    current_selection: dict[str, str],
    active_domains_map: dict[str, object],
    direct_title: str,
    ai_title: str,
    other_title: str,
    ipv6_available: bool | None = None,
) -> HostsServicesCatalogPlan:
    from hosts.proxy_domains import get_services_profile_index

    if ipv6_available is None:
        from hosts.ipv6_detection import is_ipv6_available

        ipv6_available = is_ipv6_available()

    profile_index = get_services_profile_index()
    all_dns_profiles = [
        p
        for p in (profile_index.get("dns_profiles") or [])
        if isinstance(p, str) and p.strip()
    ]
    profile_names = profile_index.get("dns_profile_names") if isinstance(profile_index, dict) else {}
    if not isinstance(profile_names, dict):
        profile_names = {}
    profile_labels = {
        profile_name: _DNS_PROFILE_IP_SUFFIX.sub(
            "",
            (profile_names.get(profile_name) or profile_name or "").strip(),
        ).strip()
        for profile_name in all_dns_profiles
    }
    ordered_services = list(profile_index.get("services") or [])
    raw_categories = profile_index.get("category_by_service") or {}
    raw_icons = profile_index.get("icon_by_service") or {}
    category_by_service = dict(raw_categories) if isinstance(raw_categories, dict) else {}
    icon_by_service = dict(raw_icons) if isinstance(raw_icons, dict) else {}

    raw_available = profile_index.get("available_by_service") or {}
    raw_has_proxy = profile_index.get("has_proxy_by_service") or {}
    raw_domain_names = profile_index.get("domain_names_by_service") or {}
    raw_profile_domain_ip_candidates = profile_index.get("profile_domain_ip_candidates_by_service") or {}
    available_profiles_by_service = dict(raw_available) if isinstance(raw_available, dict) else {}
    service_has_proxy_by_service = dict(raw_has_proxy) if isinstance(raw_has_proxy, dict) else {}
    domain_names_by_service = dict(raw_domain_names) if isinstance(raw_domain_names, dict) else {}
    profile_domain_ip_candidates_by_service = (
        dict(raw_profile_domain_ip_candidates) if isinstance(raw_profile_domain_ip_candidates, dict) else {}
    )
    direct_profile = profile_index.get("direct_profile")
    direct_profile = direct_profile if isinstance(direct_profile, str) and direct_profile.strip() else None

    no_geohide: list[str] = []
    ai: list[str] = []
    other: list[str] = []
    for service_name in ordered_services:
        if not service_has_proxy_by_service.get(service_name, False):
            no_geohide.append(service_name)
        elif category_by_service.get(service_name) == "ai":
            ai.append(service_name)
        else:
            other.append(service_name)

    sync_plan = build_selection_sync_plan(
        service_names=ordered_services,
        active_domains_map=active_domains_map,
        available_profiles_by_service=available_profiles_by_service,
        service_has_proxy_by_service=service_has_proxy_by_service,
        direct_profile=direct_profile,
        domain_names_by_service=domain_names_by_service,
        profile_domain_ip_candidates_by_service=profile_domain_ip_candidates_by_service,
    )
    current_selection = dict(current_selection or {})
    new_selection: dict[str, str] = {}

    def get_common_dns_profiles(service_names: list[str]) -> list[str]:
        common: set[str] | None = None
        for service_name in service_names:
            available = {
                p
                for p in (available_profiles_by_service.get(service_name) or [])
                if isinstance(p, str) and p.strip()
            }
            if common is None:
                common = available
            else:
                common &= available
            if not common:
                return []
        if not common:
            return []
        return [profile for profile in all_dns_profiles if profile in common]

    def label_for_profile(profile_name: str) -> str:
        return str(profile_labels.get(profile_name) or "").strip()

    groups: list[HostsServiceGroupPlan] = []
    for title, names, direct_only in (
        (direct_title, no_geohide, True),
        (ai_title, ai, False),
        (other_title, other, False),
    ):
        if not names:
            continue

        common_profiles = [
            (profile_name, label)
            for profile_name in get_common_dns_profiles(names)
            for label in (label_for_profile(profile_name),)
            if label
        ]

        rows: list[HostsServiceRowPlan] = []
        for service_name in names:
            entry = sync_plan.entries.get(service_name)
            available_profiles = list(entry.available_profiles) if entry is not None else []
            saved_profile = current_selection.get(service_name)
            selected_profile: str | None = None
            toggle_checked = False
            toggle_enabled = bool(entry.toggle_enabled) if entry is not None else False
            unavailable_reason: str | None = None

            ipv6_blocked = not ipv6_available and _service_is_ipv6_only(
                profile_domain_ip_candidates_by_service.get(service_name) or {}
            )
            if ipv6_blocked:
                # Без IPv6 записи такого сервиса всё равно не применятся; тумблер
                # гасится, но сохранённый выбор пользователя не трогаем, чтобы он
                # вернулся сам при появлении IPv6.
                toggle_enabled = False
                unavailable_reason = IPV6_UNAVAILABLE_REASON
                if saved_profile is not None:
                    new_selection[service_name] = saved_profile
            else:
                inferred_profile = entry.selected_profile if entry is not None else None
                if inferred_profile in available_profiles:
                    selected_profile = inferred_profile
                    new_selection[service_name] = inferred_profile
                elif saved_profile in available_profiles and not bool(entry and entry.has_active_domains):
                    selected_profile = saved_profile
                    new_selection[service_name] = saved_profile

                if entry is not None and entry.direct_only:
                    toggle_checked = bool(selected_profile and selected_profile == direct_profile)
                elif entry is not None:
                    toggle_checked = bool(entry.toggle_checked)

            icon = icon_by_service.get(service_name, ("fa5s.globe", None))
            if not isinstance(icon, (list, tuple)) or len(icon) != 2:
                icon = ("fa5s.globe", None)
            rows.append(
                HostsServiceRowPlan(
                    service_name=service_name,
                    icon_name=str(icon[0] or "fa5s.globe"),
                    icon_color=str(icon[1]) if icon[1] is not None else None,
                    direct_only=bool(entry.direct_only) if entry is not None else direct_only,
                    available_profiles=available_profiles,
                    profile_items=[
                        (profile_name, label)
                        for profile_name in available_profiles
                        for label in (label_for_profile(profile_name),)
                        if label
                    ],
                    selected_profile=selected_profile,
                    toggle_enabled=toggle_enabled,
                    toggle_checked=toggle_checked,
                    unavailable_reason=unavailable_reason,
                )
            )

        groups.append(
            HostsServiceGroupPlan(
                title=title,
                direct_only=direct_only,
                service_names=list(names),
                common_profiles=common_profiles,
                rows=rows,
            )
        )

    return HostsServicesCatalogPlan(
        groups=groups,
        new_selection=new_selection,
        selection_changed=dict(current_selection) != new_selection,
    )


