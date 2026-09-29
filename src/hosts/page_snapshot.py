"""Снимок всего, что нужно странице Hosts, за один проход в фоновом потоке.

Снимок только читает: каталог сервисов, текст системного hosts и признаки
доступа. Файл hosts при этом не создаётся и не меняется.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from hosts.hosts_blocks import BLOCK_ZAPRETGUI, HostsBlock, mapping_domains, parse_hosts_blocks


CATEGORY_DIRECT = "direct"
CATEGORY_AI = "ai"
CATEGORY_OTHER = "other"


@dataclass(frozen=True, slots=True)
class HostsServiceEntry:
    name: str
    category: str
    icon_name: str
    icon_color: str | None
    # Профили, которые можно выбрать. У сервисов «напрямую» это один профиль hosts.
    profiles: tuple[str, ...]
    # Что сейчас записано в блоке ZapretGUI (None — сервис выключен).
    current: str | None
    unavailable_reason: str = ""

    @property
    def is_direct(self) -> bool:
        return self.category == CATEGORY_DIRECT


@dataclass(frozen=True, slots=True)
class HostsPageSnapshot:
    services: tuple[HostsServiceEntry, ...]
    # DNS-профили (id, подпись) в порядке каталога, без профиля «напрямую».
    dns_profiles: tuple[tuple[str, str], ...]
    direct_profile: str | None
    # (сервис, профиль) -> строки (домен, адрес), ровно как их запишет HostsManager.
    rows: dict[tuple[str, str], tuple[tuple[str, str], ...]] = field(default_factory=dict)
    blocks: tuple[HostsBlock, ...] = ()
    ipv6_available: bool = True
    hosts_path: str = ""
    hosts_exists: bool = True
    readable: bool = True
    read_only: bool = False
    adobe_active: bool = False

    def block(self, kind: str) -> HostsBlock | None:
        for block in self.blocks:
            if block.kind == kind:
                return block
        return None

    def service(self, name: str) -> HostsServiceEntry | None:
        for entry in self.services:
            if entry.name == name:
                return entry
        return None


def _managed_domain_ip_map(blocks: list[HostsBlock]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for block in blocks:
        if block.kind != BLOCK_ZAPRETGUI:
            continue
        for line in block.lines:
            parts = line.partition("#")[0].split()
            if len(parts) < 2:
                continue
            ip = parts[0]
            for domain in parts[1:]:
                values = result.setdefault(domain.casefold(), [])
                if ip not in values:
                    values.append(ip)
    return result


def _row_keys(rows, *, ipv6_available: bool) -> set[tuple[str, str]]:
    """Строки (домен, адрес) в том виде, в каком их реально запишет HostsManager."""
    from hosts.ipv6_detection import is_ipv6_address

    return {
        (str(domain).casefold(), str(ip).casefold())
        for domain, ip in rows
        if ipv6_available or not is_ipv6_address(str(ip))
    }


def _written_profile(
    service_name: str,
    inferred: str,
    profiles: tuple[str, ...],
    *,
    rows: dict[tuple[str, str], tuple[tuple[str, str], ...]],
    block_keys: set[tuple[str, str]],
    saved: str | None,
    ipv6_available: bool,
) -> str:
    """Какой профиль сервиса на самом деле записан в блок.

    Общее определение берёт первый профиль, все строки которого есть в блоке.
    Но у одного профиля адреса бывают частью другого (comss_dns у Truth Social
    = адреса xbox_dns и ещё два), а у разных профилей — совпадать целиком.
    Поэтому из профилей, все строки которых записаны, берётся самый полный,
    а при равенстве — тот, что пользователь сам выбрал при записи.
    """
    best = inferred
    best_rank: tuple[int, bool, bool] | None = None
    for profile_id in profiles:
        keys = _row_keys(rows.get((service_name, profile_id), ()), ipv6_available=ipv6_available)
        if not keys or not keys <= block_keys:
            continue
        rank = (len(keys), profile_id == saved, profile_id == inferred)
        if best_rank is None or rank > best_rank:
            best, best_rank = profile_id, rank
    return best


def _is_adobe_active(blocks: list[HostsBlock]) -> bool:
    from hosts.adobe_domains import ADOBE_DOMAINS

    adobe = {domain.casefold() for domain in ADOBE_DOMAINS}
    return any(
        domain.casefold() in adobe
        for block in blocks
        for line in block.lines
        for domain in mapping_domains(line)
    )


@dataclass(frozen=True, slots=True)
class _CatalogPart:
    """Всё, что зависит только от каталога: считается один раз на его версию."""

    dns_profiles: tuple[tuple[str, str], ...]
    direct_profile: str | None
    rows: dict[tuple[str, str], tuple[tuple[str, str], ...]]


_LOCK = threading.Lock()
_CATALOG_PART: tuple[object, _CatalogPart] | None = None
_SNAPSHOT: tuple[object, HostsPageSnapshot] | None = None


def _build_catalog_part() -> _CatalogPart:
    from hosts.page_plans import format_dns_profile_label
    from hosts.proxy_domains import get_service_domain_ip_rows, get_services_profile_index

    profile_index = get_services_profile_index()
    direct_profile = profile_index.get("direct_profile") or None
    dns_profiles = tuple(
        (profile_id, format_dns_profile_label(profile_id) or profile_id)
        for profile_id in (profile_index.get("dns_profiles") or [])
        if isinstance(profile_id, str) and profile_id and profile_id != direct_profile
    )
    has_proxy = profile_index.get("has_proxy_by_service") or {}
    available = profile_index.get("available_by_service") or {}
    rows: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    for service_name in profile_index.get("services") or []:
        if has_proxy.get(service_name):
            profiles = list(available.get(service_name) or [])
        else:
            profiles = [direct_profile] if direct_profile else []
        for profile_id in profiles:
            rows[(service_name, profile_id)] = tuple(
                (str(domain), str(ip))
                for domain, ip in (get_service_domain_ip_rows(service_name, profile_id) or [])
            )
    return _CatalogPart(dns_profiles=dns_profiles, direct_profile=direct_profile, rows=rows)


def _catalog_part(catalog_key: object) -> _CatalogPart:
    global _CATALOG_PART
    cached = _CATALOG_PART
    if cached is not None and cached[0] == catalog_key:
        return cached[1]
    part = _build_catalog_part()
    with _LOCK:
        _CATALOG_PART = (catalog_key, part)
    return part


def build_page_snapshot(
    *,
    hosts_text: str,
    hosts_path: str,
    hosts_exists: bool,
    readable: bool,
    read_only: bool,
    ipv6_available: bool,
    saved_selection: dict[str, str] | None = None,
    catalog_key: object = None,
) -> HostsPageSnapshot:
    """Собирает снимок из уже прочитанного текста hosts и каталога.

    saved_selection — выбор, сохранённый при последней записи; нужен только
    чтобы различить профили с одинаковыми адресами.
    """
    from hosts.page_plans import build_services_catalog_plan

    catalog = _catalog_part(catalog_key) if catalog_key is not None else _build_catalog_part()
    direct_profile = catalog.direct_profile
    blocks = parse_hosts_blocks(hosts_text)
    saved_selection = dict(saved_selection or {})
    block_keys = {
        (domain.casefold(), ip.casefold())
        for domain, ips in _managed_domain_ip_map(blocks).items()
        for ip in ips
    }

    # Выбор выводится только из блока ZapretGUI: сохранённый, но не записанный
    # выбор не показывается включённым.
    plan = build_services_catalog_plan(
        current_selection={},
        active_domains_map=_managed_domain_ip_map(blocks),
        direct_title=CATEGORY_DIRECT,
        ai_title=CATEGORY_AI,
        other_title=CATEGORY_OTHER,
        ipv6_available=ipv6_available,
    )

    services: list[HostsServiceEntry] = []
    for group in plan.groups:
        category = str(group.title)
        for row in group.rows:
            if row.direct_only:
                profiles = (direct_profile,) if direct_profile else ()
                current = direct_profile if row.toggle_checked else None
            else:
                profiles = tuple(row.available_profiles)
                current = row.selected_profile if row.selected_profile in profiles else None
                if current is not None:
                    current = _written_profile(
                        row.service_name,
                        current,
                        profiles,
                        rows=catalog.rows,
                        block_keys=block_keys,
                        saved=saved_selection.get(row.service_name),
                        ipv6_available=ipv6_available,
                    )
            services.append(
                HostsServiceEntry(
                    name=row.service_name,
                    category=category,
                    icon_name=row.icon_name,
                    icon_color=row.icon_color,
                    profiles=profiles,
                    current=current,
                    unavailable_reason="ipv6" if row.unavailable_reason else "",
                )
            )

    return HostsPageSnapshot(
        services=tuple(services),
        dns_profiles=catalog.dns_profiles,
        direct_profile=direct_profile,
        rows=catalog.rows,
        blocks=tuple(blocks),
        ipv6_available=bool(ipv6_available),
        hosts_path=str(hosts_path),
        hosts_exists=bool(hosts_exists),
        readable=bool(readable),
        read_only=bool(read_only),
        adobe_active=_is_adobe_active(blocks),
    )


def load_page_snapshot() -> HostsPageSnapshot:
    """Читает каталог и hosts (только чтение) и собирает снимок страницы.

    Если ни hosts, ни каталог не менялись (дата и размер), отдаёт готовый
    снимок из памяти без чтения файлов.
    """
    global _SNAPSHOT
    from hosts.hosts import HOSTS_PATH, _get_hosts_sig, is_file_readonly, safe_read_hosts_file
    from hosts.ipv6_detection import is_ipv6_available
    from hosts.proxy_domains import get_hosts_catalog_signature, load_user_hosts_selection

    catalog_key = get_hosts_catalog_signature()
    ipv6_available = bool(is_ipv6_available())
    hosts_sig = _get_hosts_sig(HOSTS_PATH)
    saved_selection = dict(load_user_hosts_selection() or {})
    # Выбор входит в ключ: запись профиля с теми же адресами файл не меняет.
    key = (hosts_sig, catalog_key, ipv6_available, tuple(sorted(saved_selection.items())))
    cached = _SNAPSHOT
    if cached is not None and cached[0] == key:
        return cached[1]

    hosts_exists = hosts_sig is not None
    text = safe_read_hosts_file()
    snapshot = build_page_snapshot(
        hosts_text=text or "",
        hosts_path=str(HOSTS_PATH),
        hosts_exists=hosts_exists,
        readable=text is not None,
        read_only=bool(hosts_exists and is_file_readonly(HOSTS_PATH)),
        ipv6_available=ipv6_available,
        saved_selection=saved_selection,
        catalog_key=catalog_key,
    )
    with _LOCK:
        _SNAPSHOT = (key, snapshot)
    return snapshot


def peek_page_snapshot() -> HostsPageSnapshot | None:
    """Последний готовый снимок из памяти, без чтения файлов.

    Страница рисует его сразу при открытии, а свежесть проверяет фоновая задача.
    """
    cached = _SNAPSHOT
    return cached[1] if cached is not None else None


__all__ = [
    "CATEGORY_AI",
    "CATEGORY_DIRECT",
    "CATEGORY_OTHER",
    "HostsPageSnapshot",
    "HostsServiceEntry",
    "build_page_snapshot",
    "load_page_snapshot",
    "peek_page_snapshot",
]
