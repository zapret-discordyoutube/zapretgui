"""Замена устаревших адресов DNS-провайдеров в настройках сетевых адаптеров.

Пользователь когда-то выбрал провайдера (Xbox DNS, dns.malw.link), и Windows
запомнила его адреса в ручных настройках адаптера. Когда провайдер меняет
адреса, старые перестают работать. При запуске программа находит на
адаптерах только адреса из OUTDATED_DNS_ADDRESS_REPLACEMENTS и меняет их на
новые. Автоматически полученные (DHCP) и любые другие адреса не трогаются.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from log.log import log


def _canonical_ip(value: str) -> str:
    text = str(value or "").strip()
    try:
        return ipaddress.ip_address(text).compressed
    except ValueError:
        return text.casefold()


def plan_dns_server_migration(
    servers: list[str],
    replacements: Mapping[str, str],
) -> list[str] | None:
    """Возвращает новый список DNS или None, если менять нечего.

    Порядок и чужие адреса сохраняются, старые адреса заменяются на месте,
    повторы после замены убираются.
    """
    canonical_replacements = {
        _canonical_ip(old): str(new).strip()
        for old, new in dict(replacements or {}).items()
        if str(old or "").strip() and str(new or "").strip()
    }
    current = [str(item).strip() for item in (servers or []) if str(item or "").strip()]
    replaced_any = False
    result: list[str] = []
    seen: set[str] = set()
    for server in current:
        replacement = canonical_replacements.get(_canonical_ip(server))
        if replacement is not None:
            replaced_any = True
            server = replacement
        key = _canonical_ip(server)
        if key in seen:
            continue
        seen.add(key)
        result.append(server)
    return result if replaced_any else None


def migrate_outdated_dns_addresses(dns_manager=None, replacements: Mapping[str, str] | None = None) -> list[str]:
    """Меняет старые адреса провайдеров на всех поддерживаемых адаптерах.

    Возвращает список изменений в виде строк для лога.
    """
    if replacements is None:
        from dns.dns_providers import OUTDATED_DNS_ADDRESS_REPLACEMENTS

        replacements = OUTDATED_DNS_ADDRESS_REPLACEMENTS
    if dns_manager is None:
        from dns.dns_core import DNSManager

        dns_manager = DNSManager()

    changes: list[str] = []
    for adapter_name, _description in dns_manager.get_network_adapters_fast(
        include_ignored=False,
        include_disconnected=True,
    ):
        for family in ("IPv4", "IPv6"):
            current = list(dns_manager.get_current_dns(adapter_name, family) or [])
            new_servers = plan_dns_server_migration(current, replacements)
            if new_servers is None:
                continue
            success, message = dns_manager.set_dns_servers(adapter_name, new_servers, family)
            summary = f"{adapter_name} ({family}): {', '.join(current)} -> {', '.join(new_servers)}"
            if success:
                log(f"DNS: старые адреса провайдера заменены на новые: {summary}", "INFO")
                changes.append(summary)
            else:
                log(f"DNS: не удалось заменить старые адреса провайдера: {summary}: {message}", "WARNING")
    return changes


__all__ = ["migrate_outdated_dns_addresses", "plan_dns_server_migration"]
