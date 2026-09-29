from __future__ import annotations

from dataclasses import dataclass

from app.ui_texts import tr as tr_catalog


@dataclass(slots=True)
class NetworkProviderDnsPlan:
    valid: bool
    ipv4: list[str]
    ipv6: list[str]
    log_level: str | None
    log_message: str


@dataclass(slots=True)
class NetworkDnsApplyResultPlan:
    should_refresh: bool
    log_level: str | None
    log_message: str
    adapter_count: int = 0
    success_count: int = 0


@dataclass(slots=True)
class NetworkFlushDnsCacheResultPlan:
    success: bool
    infobar_level: str | None
    title: str
    content: str


@dataclass(slots=True)
class NetworkIspDnsWarningPlan:
    should_show: bool
    title: str
    content: str
    action_text: str
    dismiss_text: str


def normalize_dns_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            item_s = str(item).strip()
            if item_s:
                result.append(item_s)
        return result
    return []


def build_provider_dns_plan(
    *,
    name: str,
    data: dict,
    ipv6_available: bool,
) -> NetworkProviderDnsPlan:
    ipv4 = normalize_dns_list(data.get("ipv4", []))
    ipv6 = normalize_dns_list(data.get("ipv6", [])) if ipv6_available else []
    if not ipv4 and not ipv6:
        return NetworkProviderDnsPlan(
            valid=False,
            ipv4=[],
            ipv6=[],
            log_level="WARNING",
            log_message=f"DNS: у провайдера {name} нет DNS адресов для текущей системы",
        )

    return NetworkProviderDnsPlan(
        valid=True,
        ipv4=ipv4,
        ipv6=ipv6,
        log_level=None,
        log_message="",
    )


def build_auto_dns_apply_result_plan(*, adapter_count: int, success_count: int) -> NetworkDnsApplyResultPlan:
    log_message = ""
    log_level = None
    if adapter_count > 0 and success_count == adapter_count:
        log_message = f"DNS: Автоматический (IPv4+IPv6) применён к {success_count} адаптерам"
        log_level = "INFO"
    return NetworkDnsApplyResultPlan(
        should_refresh=bool(adapter_count),
        log_level=log_level,
        log_message=log_message,
        adapter_count=int(adapter_count),
        success_count=int(success_count),
    )


def build_provider_dns_apply_result_plan(
    *,
    name: str,
    adapter_count: int,
    success_count: int,
    ipv6_available: bool,
    ipv6: list[str],
) -> NetworkDnsApplyResultPlan:
    log_message = ""
    log_level = None
    if adapter_count > 0 and success_count == adapter_count:
        if ipv6_available and ipv6:
            log_message = f"DNS: {name} (IPv4+IPv6) применён к {success_count} адаптерам"
        else:
            log_message = f"DNS: {name} применён к {success_count} адаптерам"
        log_level = "INFO"
    return NetworkDnsApplyResultPlan(
        should_refresh=bool(adapter_count),
        log_level=log_level,
        log_message=log_message,
        adapter_count=int(adapter_count),
        success_count=int(success_count),
    )


def build_flush_dns_cache_result_plan(
    *,
    success: bool,
    message: str,
    language: str = "ru",
) -> NetworkFlushDnsCacheResultPlan:
    if success:
        return NetworkFlushDnsCacheResultPlan(
            success=True,
            infobar_level=None,
            title="",
            content="",
        )
    return NetworkFlushDnsCacheResultPlan(
        success=False,
        infobar_level="warning",
        title=tr_catalog("page.network.error.title", language=language, default="Ошибка"),
        content=tr_catalog(
            "page.network.error.flush_cache_failed",
            language=language,
            default="Не удалось очистить кэш: {error}",
        ).format(error=message),
    )


def should_show_isp_dns_warning(
    adapters: list[tuple[str, str]],
    dns_info: dict[str, dict[str, list[str]]],
    *,
    warning_already_shown: bool,
    normalize_alias_fn,
) -> bool:
    if warning_already_shown:
        return False

    has_adapters = False
    all_dhcp = True
    for name, _desc in adapters:
        has_adapters = True
        clean = normalize_alias_fn(name)
        adapter_data = dns_info.get(clean, {"ipv4": [], "ipv6": []})
        ipv4 = normalize_dns_list(adapter_data.get("ipv4", []))
        if ipv4:
            all_dhcp = False
            break
    return bool(has_adapters and all_dhcp)


def build_isp_dns_warning_plan(
    adapters: list[tuple[str, str]],
    dns_info: dict[str, dict[str, list[str]]],
    *,
    warning_already_shown: bool,
    normalize_alias_fn,
    language: str = "ru",
) -> NetworkIspDnsWarningPlan:
    should_show = should_show_isp_dns_warning(
        adapters,
        dns_info,
        warning_already_shown=warning_already_shown,
        normalize_alias_fn=normalize_alias_fn,
    )
    return NetworkIspDnsWarningPlan(
        should_show=should_show,
        title=tr_catalog(
            "page.network.isp_dns.infobar.title",
            language=language,
            default="DNS от провайдера",
        ),
        content=tr_catalog(
            "page.network.isp_dns.infobar.content",
            language=language,
            default=(
                "У вас установлен DNS от провайдера (получен автоматически через DHCP). "
                "Провайдерский DNS может подменять ответы и мешать обходу блокировок.\n\n"
                "Можно вручную применить публичный DNS Quad9 или выбрать другой DNS из списка ниже."
            ),
        ),
        action_text=tr_catalog(
            "page.network.isp_dns.infobar.action",
            language=language,
            default="Установить рекомендуемый DNS",
        ),
        dismiss_text=tr_catalog(
            "page.network.isp_dns.infobar.dismiss",
            language=language,
            default="Нет, спасибо",
        ),
    )


# ── Текущий DNS выбранных адаптеров ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CurrentDnsPlan:
    """Что сейчас стоит на отмеченных адаптерах.

    kind: "none" — адаптеры не отмечены или ещё не загружены;
    "auto" — DNS получается автоматически (DHCP); "provider" — один из
    известных серверов (provider — его имя); "custom" — адреса, которых нет
    в списке; "mixed" — на отмеченных адаптерах стоят разные DNS.
    """

    kind: str
    provider: str | None = None
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()


def find_provider_for_dns(providers: dict, ipv4: list[str], ipv6: list[str]) -> str | None:
    """Имя сервера из списка, чей основной адрес стоит первым на адаптере."""
    for group in providers.values():
        for name, data in group.items():
            provider_v4 = normalize_dns_list(data.get("ipv4", []))
            provider_v6 = normalize_dns_list(data.get("ipv6", []))
            if ipv4 and provider_v4 and provider_v4[0] == ipv4[0]:
                return name
            if not ipv4 and ipv6 and provider_v6 and provider_v6[0] == ipv6[0]:
                return name
    return None


def build_current_dns_plan(
    *,
    selected_adapters: list[str],
    dns_info: dict[str, dict[str, list[str]]],
    providers: dict,
    normalize_alias_fn,
) -> CurrentDnsPlan:
    plans: list[CurrentDnsPlan] = []
    for adapter in selected_adapters:
        data = dns_info.get(normalize_alias_fn(adapter), {}) or {}
        ipv4 = normalize_dns_list(data.get("ipv4", []))
        ipv6 = normalize_dns_list(data.get("ipv6", []))
        if not ipv4 and not ipv6:
            plans.append(CurrentDnsPlan(kind="auto"))
            continue
        provider = find_provider_for_dns(providers, ipv4, ipv6)
        plans.append(
            CurrentDnsPlan(
                kind="provider" if provider else "custom",
                provider=provider,
                ipv4=tuple(ipv4),
                ipv6=tuple(ipv6),
            )
        )
    if not plans:
        return CurrentDnsPlan(kind="none")
    first = plans[0]
    same = all(
        (plan.kind, plan.provider, plan.ipv4[:1], plan.ipv6[:1])
        == (first.kind, first.provider, first.ipv4[:1], first.ipv6[:1])
        for plan in plans[1:]
    )
    return first if same else CurrentDnsPlan(kind="mixed")
