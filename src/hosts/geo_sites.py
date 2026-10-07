"""Гео-сайты: сервисы, которые сами не пускают посетителей из России.

В каталоге hosts это сервисы с DNS-профилем (ChatGPT, Claude, Spotify и
другие). Провайдер их не режет — отказывает сам сервис, поэтому стратегия
Zapret им не помогает. Помогает DNS-профиль в «Редакторе hosts» или смена DNS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


def _site_key(host: object) -> str:
    value = str(host or "").strip().lower().rstrip(".")
    return value[4:] if value.startswith("www.") else value


@dataclass(frozen=True, slots=True)
class GeoSites:
    """Адреса гео-сайтов из каталога hosts: адрес → название сервиса."""

    service_by_site: Mapping[str, str]

    def service_for(self, host: str) -> str:
        """Название сервиса, если адрес — его сайт или поддомен сайта; иначе пусто."""
        labels = _site_key(host).split(".")
        for start in range(len(labels) - 1):
            service = self.service_by_site.get(".".join(labels[start:]))
            if service:
                return service
        return ""


def build_geo_sites(profile_index: Mapping[str, object]) -> GeoSites:
    """Собирает гео-сайты из индекса каталога (``get_services_profile_index``)."""
    has_proxy = profile_index.get("has_proxy_by_service") or {}
    domain_names = profile_index.get("domain_names_by_service") or {}
    service_by_site: dict[str, str] = {}
    for service_name in profile_index.get("services") or ():
        if not has_proxy.get(service_name, False):
            continue
        for domain in domain_names.get(service_name) or ():
            key = _site_key(domain)
            if key:
                service_by_site.setdefault(key, str(service_name))
    return GeoSites(service_by_site)


# Доля списка, с которой он считается списком гео-сервиса. В общем списке на
# тысячи сайтов пара гео-адресов встречается случайно — о нём молчим.
_GEO_LIST_SHARE_DIVISOR = 20


def geo_services_for_site_list(geo_sites: GeoSites, text: str) -> tuple[str, ...]:
    """Гео-сервисы, которым посвящён список сайтов (текст hostlist-файла).

    Пустой ответ — список не про гео-сервис: его сайтам помогает стратегия.
    Сервисы идут по убыванию числа их адресов в списке.
    """
    total = 0
    counts: dict[str, int] = {}
    for line in str(text or "").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        total += 1
        service = geo_sites.service_for(entry)
        if service:
            counts[service] = counts.get(service, 0) + 1
    if not counts or sum(counts.values()) * _GEO_LIST_SHARE_DIVISOR < total:
        return ()
    return tuple(sorted(counts, key=lambda name: (-counts[name], name)))


def load_geo_sites() -> GeoSites:
    """Читает каталог hosts (файл) — звать вне UI-потока."""
    from hosts.proxy_domains import get_services_profile_index

    return build_geo_sites(get_services_profile_index())


def load_geo_services_for_site_list(text: str) -> tuple[str, ...]:
    """Читает каталог hosts (файл) — звать вне UI-потока."""
    return geo_services_for_site_list(load_geo_sites(), text)


__all__ = [
    "GeoSites",
    "build_geo_sites",
    "geo_services_for_site_list",
    "load_geo_services_for_site_list",
    "load_geo_sites",
]
