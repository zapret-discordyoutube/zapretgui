"""Business facade for the shipped read-only Hosts SQLite catalog."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from config.runtime_layout import APPLICATION_PATHS, PACKAGED_RUNTIME
from hosts.catalog_repository import (
    CATALOG_FILE_NAME,
    HOSTS_PROFILE_ID,
    HostsCatalog,
    empty_catalog,
    file_content_signature,
    load_catalog,
)
from settings import store as settings_store


_SERVICE_MODE_DNS = "dns"
_SERVICE_MODE_HOSTS = "hosts"
_CACHE_LOCK = threading.RLock()
_CACHE: HostsCatalog | None = None
_CACHE_SIG: tuple[int, int] | None = None
_CACHE_PATH: Path | None = None
_CACHE_PROFILE_INDEX: dict[str, object] | None = None
_RECENT_SIG_PATH: Path | None = None
_RECENT_SIG: tuple[int, int] | None = None
_RECENT_SIG_CHECKED_AT = 0.0
_RECENT_SIG_TTL_SECONDS = 1.0
_MISSING_CATALOG_LOGGED = False
_LAST_LOAD_ERROR: tuple[Path, tuple[int, int] | None] | None = None

# Explicit test seam.  Production always receives the installation root.
MAIN_DIRECTORY = str(APPLICATION_PATHS.root)


def _log(message: str, level: str = "INFO") -> None:
    try:
        from log.log import log as log_impl  # type: ignore

        log_impl(message, level)
    except Exception:
        print(f"[{level}] {message}")


def _clean_str(value: object) -> str:
    return str(value or "").strip()


def _get_hosts_catalog_path() -> Path:
    if PACKAGED_RUNTIME:
        return Path(MAIN_DIRECTORY) / "system" / CATALOG_FILE_NAME
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "private_zapretgui" / "resources" / "system" / CATALOG_FILE_NAME


def get_hosts_catalog_path() -> Path:
    return _get_hosts_catalog_path()


def _remember_recent_signature(path: Path, signature: tuple[int, int]) -> None:
    global _RECENT_SIG_PATH, _RECENT_SIG, _RECENT_SIG_CHECKED_AT
    _RECENT_SIG_PATH = path
    _RECENT_SIG = signature
    _RECENT_SIG_CHECKED_AT = time.monotonic()


def _recent_signature(path: Path) -> tuple[int, int] | None:
    if _RECENT_SIG_PATH != path or _RECENT_SIG is None:
        return None
    if time.monotonic() - _RECENT_SIG_CHECKED_AT > _RECENT_SIG_TTL_SECONDS:
        return None
    return _RECENT_SIG


def _path_signature(path: Path) -> tuple[int, int] | None:
    try:
        signature = file_content_signature(path)
    except (OSError, ValueError):
        return None
    _remember_recent_signature(path, signature)
    return signature


def _load_catalog() -> HostsCatalog:
    global _CACHE, _CACHE_SIG, _CACHE_PATH, _CACHE_PROFILE_INDEX
    global _MISSING_CATALOG_LOGGED, _LAST_LOAD_ERROR

    path = _get_hosts_catalog_path()
    with _CACHE_LOCK:
        signature = _recent_signature(path)
        if signature is None and path.is_file():
            signature = _path_signature(path)

        if (
            _CACHE is not None
            and _CACHE_PATH == path
            and _CACHE_SIG == signature
        ):
            return _CACHE

        if not path.is_file():
            if not _MISSING_CATALOG_LOGGED:
                _log(f"База hosts-каталога не найдена: {path}", "WARNING")
                _MISSING_CATALOG_LOGGED = True
            _CACHE = empty_catalog()
            _CACHE_PATH = path
            _CACHE_SIG = None
            _CACHE_PROFILE_INDEX = None
            return _CACHE

        _MISSING_CATALOG_LOGGED = False
        try:
            catalog = load_catalog(path)
        except Exception as exc:
            error_key = (path, signature)
            if _LAST_LOAD_ERROR != error_key:
                _log(f"База hosts-каталога отклонена: {exc}", "ERROR")
                _LAST_LOAD_ERROR = error_key
            catalog = empty_catalog()
        else:
            _LAST_LOAD_ERROR = None

        _CACHE = catalog
        _CACHE_PATH = path
        _CACHE_SIG = signature
        _CACHE_PROFILE_INDEX = None
        return catalog


def invalidate_hosts_catalog_cache() -> None:
    global _CACHE, _CACHE_SIG, _CACHE_PATH, _CACHE_PROFILE_INDEX
    global _RECENT_SIG_PATH, _RECENT_SIG, _RECENT_SIG_CHECKED_AT
    with _CACHE_LOCK:
        _CACHE = None
        _CACHE_SIG = None
        _CACHE_PATH = None
        _CACHE_PROFILE_INDEX = None
        _RECENT_SIG_PATH = None
        _RECENT_SIG = None
        _RECENT_SIG_CHECKED_AT = 0.0


def get_hosts_catalog_signature() -> tuple[str, int, int] | None:
    """Return (path, content revision, file size) outside the GUI thread."""
    path = _get_hosts_catalog_path()
    if not path.is_file():
        return None
    with _CACHE_LOCK:
        signature = _recent_signature(path)
        if signature is None:
            signature = _path_signature(path)
    if signature is None:
        return None
    revision, size = signature
    return str(path), revision, size


def get_dns_profiles() -> list[str]:
    return list(_load_catalog().dns_profiles)


def get_dns_profile_display_name(profile_id: str) -> str:
    profile_id = _clean_str(profile_id)
    if not profile_id:
        return ""
    return _load_catalog().dns_profile_names.get(profile_id, profile_id)


def get_all_services() -> list[str]:
    return list(_load_catalog().service_order)


def get_service_domain_names(service_name: str) -> list[str]:
    entries = _load_catalog().service_entries.get(service_name, []) or []
    result: list[str] = []
    seen: set[str] = set()
    for domain, _ips in entries:
        key = domain.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(domain)
    return result


def get_service_domains(service_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for domain, ips in _load_catalog().service_entries.get(service_name, []) or []:
        domain_key = _clean_str(domain).casefold()
        if not domain_key or domain_key in result:
            continue
        for ip_address in ips or []:
            value = _clean_str(ip_address)
            if value:
                result[domain_key] = value
                break
    return result


def _get_complete_profile_rows(
    catalog: HostsCatalog,
    service_name: str,
    profile_id: str,
) -> list[tuple[str, str]]:
    if profile_id not in catalog.dns_profiles:
        return []
    profile_index = catalog.dns_profiles.index(profile_id)
    entries = catalog.service_entries.get(service_name, []) or []
    if not entries:
        return []

    required_domains = {domain.casefold() for domain, _ips in entries if domain}
    result: list[tuple[str, str]] = []
    covered_domains: set[str] = set()
    for domain, ips in entries:
        if not ips or profile_index >= len(ips) or not ips[profile_index]:
            continue
        result.append((domain, ips[profile_index]))
        covered_domains.add(domain.casefold())
    if not required_domains or covered_domains != required_domains:
        return []
    return result


def get_service_domain_ip_rows(service_name: str, profile_name: str) -> list[tuple[str, str]]:
    return _get_complete_profile_rows(
        _load_catalog(),
        service_name,
        _clean_str(profile_name),
    )


def _get_service_available_dns_profiles_from_catalog(
    catalog: HostsCatalog,
    service_name: str,
) -> list[str]:
    if not catalog.service_entries.get(service_name):
        return []
    return [
        profile_id
        for profile_id in catalog.dns_profiles
        if _get_complete_profile_rows(catalog, service_name, profile_id)
    ]


def get_service_available_dns_profiles(service_name: str) -> list[str]:
    return _get_service_available_dns_profiles_from_catalog(_load_catalog(), service_name)


def _infer_direct_profile_index(catalog: HostsCatalog) -> int | None:
    try:
        return catalog.dns_profiles.index(HOSTS_PROFILE_ID)
    except ValueError:
        return None


def service_has_proxy_profiles(service_name: str) -> bool:
    mode = _load_catalog().service_modes.get(_clean_str(service_name).casefold())
    return mode == _SERVICE_MODE_DNS


def _build_services_profile_index(catalog: HostsCatalog) -> dict[str, object]:
    services = list(catalog.service_order)
    profiles = list(catalog.dns_profiles)
    available_by_service: dict[str, list[str]] = {}
    profile_domain_maps_by_service: dict[str, dict[str, dict[str, str]]] = {}
    profile_domain_ip_candidates_by_service: dict[str, dict[str, dict[str, list[str]]]] = {}
    domain_names_by_service: dict[str, list[str]] = {}
    direct_index = _infer_direct_profile_index(catalog)
    direct_profile = profiles[direct_index] if direct_index is not None else None

    for service_name in services:
        entries = catalog.service_entries.get(service_name, []) or []
        domain_names: list[str] = []
        seen_domains: set[str] = set()
        rows_by_profile: dict[str, list[tuple[str, str]]] = {profile_id: [] for profile_id in profiles}
        covered_by_profile: dict[str, set[str]] = {profile_id: set() for profile_id in profiles}

        for domain, ips in entries:
            domain_key = _clean_str(domain).casefold()
            if not domain_key:
                continue
            if domain_key not in seen_domains:
                seen_domains.add(domain_key)
                domain_names.append(domain)
            for profile_index, profile_id in enumerate(profiles):
                if not ips or profile_index >= len(ips):
                    continue
                ip_value = _clean_str(ips[profile_index])
                if not ip_value:
                    continue
                rows_by_profile[profile_id].append((domain_key, ip_value))
                covered_by_profile[profile_id].add(domain_key)

        domain_names_by_service[service_name] = domain_names
        required_domains = set(seen_domains)
        service_maps: dict[str, dict[str, str]] = {}
        service_candidates: dict[str, dict[str, list[str]]] = {}
        available: list[str] = []
        for profile_id in profiles:
            rows = rows_by_profile[profile_id]
            if not required_domains or covered_by_profile[profile_id] != required_domains:
                continue
            available.append(profile_id)
            domain_map: dict[str, str] = {}
            candidates: dict[str, list[str]] = {}
            for domain_key, ip_value in rows:
                domain_map.setdefault(domain_key, ip_value)
                values = candidates.setdefault(domain_key, [])
                if ip_value not in values:
                    values.append(ip_value)
            service_maps[profile_id] = domain_map
            service_candidates[profile_id] = candidates
        available_by_service[service_name] = available
        profile_domain_maps_by_service[service_name] = service_maps
        profile_domain_ip_candidates_by_service[service_name] = service_candidates

    return {
        "dns_profiles": profiles,
        "dns_profile_names": dict(catalog.dns_profile_names),
        "services": services,
        "available_by_service": available_by_service,
        "has_proxy_by_service": {
            service_name: catalog.service_modes.get(service_name.casefold()) == _SERVICE_MODE_DNS
            for service_name in services
        },
        "category_by_service": dict(catalog.service_categories),
        "icon_by_service": dict(catalog.service_icons),
        "service_id_by_name": dict(catalog.service_id_by_name),
        "direct_profile": direct_profile,
        "domain_names_by_service": domain_names_by_service,
        "profile_domain_maps_by_service": profile_domain_maps_by_service,
        "profile_domain_ip_candidates_by_service": profile_domain_ip_candidates_by_service,
    }


def get_services_profile_index() -> dict[str, object]:
    global _CACHE_PROFILE_INDEX
    with _CACHE_LOCK:
        if _CACHE_PROFILE_INDEX is not None:
            return _CACHE_PROFILE_INDEX
    catalog = _load_catalog()
    with _CACHE_LOCK:
        if _CACHE_PROFILE_INDEX is None:
            _CACHE_PROFILE_INDEX = _build_services_profile_index(catalog)
        return _CACHE_PROFILE_INDEX


def get_service_domain_ip_map(service_name: str, profile_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for domain, ip_address in get_service_domain_ip_rows(service_name, profile_name):
        domain_key = _clean_str(domain).casefold()
        if domain_key and domain_key not in result:
            result[domain_key] = ip_address
    return result


def load_user_hosts_selection() -> dict[str, str]:
    """Translate durable service IDs from settings.sqlite3 to current names."""
    try:
        stored = dict(settings_store.get_hosts_selection() or {})
        catalog = _load_catalog()
    except Exception as exc:
        _log(f"Не удалось прочитать выбор hosts из settings.sqlite3: {exc}", "WARNING")
        return {}

    names_casefold = {name.casefold(): name for name in catalog.service_id_by_name}
    result: dict[str, str] = {}
    for key, profile_id in stored.items():
        key_text = _clean_str(key)
        service_name = catalog.service_name_by_id.get(key_text)
        if service_name is None:
            service_name = names_casefold.get(key_text.casefold())
        if service_name is None:
            continue
        if profile_id in _get_service_available_dns_profiles_from_catalog(catalog, service_name):
            result[service_name] = profile_id
    return result


def save_user_hosts_selection(selected_profiles: dict[str, str]) -> bool:
    """Store stable service IDs and retain choices for temporarily removed services."""
    try:
        catalog = _load_catalog()
        previous = dict(settings_store.get_hosts_selection() or {})
        known_ids = set(catalog.service_name_by_id)
        known_names = {name.casefold() for name in catalog.service_id_by_name}
        durable = {
            _clean_str(key): _clean_str(value)
            for key, value in previous.items()
            if _clean_str(key) not in known_ids
            and _clean_str(key).casefold() not in known_names
            and _clean_str(key)
            and _clean_str(value)
        }
        names_casefold = {name.casefold(): name for name in catalog.service_id_by_name}
        for raw_name, raw_profile in dict(selected_profiles or {}).items():
            service_name = names_casefold.get(_clean_str(raw_name).casefold())
            profile_id = _clean_str(raw_profile)
            if service_name is None:
                continue
            if profile_id not in _get_service_available_dns_profiles_from_catalog(catalog, service_name):
                continue
            durable[catalog.service_id_by_name[service_name]] = profile_id
        return bool(settings_store.set_hosts_selection(durable))
    except Exception as exc:
        _log(f"Не удалось сохранить выбор hosts в settings.sqlite3: {exc}", "WARNING")
        return False


__all__ = [
    "get_all_services",
    "get_dns_profile_display_name",
    "get_dns_profiles",
    "get_hosts_catalog_path",
    "get_hosts_catalog_signature",
    "get_service_available_dns_profiles",
    "get_service_domain_ip_map",
    "get_service_domain_ip_rows",
    "get_service_domain_names",
    "get_service_domains",
    "get_services_profile_index",
    "invalidate_hosts_catalog_cache",
    "load_user_hosts_selection",
    "save_user_hosts_selection",
    "service_has_proxy_profiles",
]
