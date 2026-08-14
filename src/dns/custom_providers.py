"""Преобразование пользовательских DNS из settings.sqlite3 в провайдеры страницы."""

from __future__ import annotations

import copy


CUSTOM_DNS_CATEGORY = "Свои DNS"


def build_dns_providers_with_custom(base_providers: dict, custom_servers: list[dict]) -> dict:
    providers = copy.deepcopy(base_providers)
    custom_group: dict[str, dict] = {}
    for server in custom_servers:
        name = str(server.get("name") or "").strip()
        ipv4 = [str(item).strip() for item in server.get("ipv4", []) if str(item).strip()]
        ipv6 = [str(item).strip() for item in server.get("ipv6", []) if str(item).strip()]
        if not name or (not ipv4 and not ipv6):
            continue
        custom_group[name] = {
            "ipv4": ipv4,
            "ipv6": ipv6,
            "desc": "Пользовательский",
            "icon": "fa5s.edit",
            "color": "#22c55e",
            "custom_id": str(server.get("id") or ""),
        }
    if custom_group:
        providers[CUSTOM_DNS_CATEGORY] = custom_group
    return providers


__all__ = ["CUSTOM_DNS_CATEGORY", "build_dns_providers_with_custom"]
