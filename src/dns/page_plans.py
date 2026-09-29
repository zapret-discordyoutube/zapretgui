"""Чистые решения страницы DNS: без Qt и без вызовов Windows."""

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
    log_level: str | None
    log_message: str
    adapter_count: int = 0
    success_count: int = 0
    error: str = ""


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
        items = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        return []
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


# ── применение ────────────────────────────────────────────────────────────


def build_provider_dns_plan(*, name: str, data: dict, ipv6_available: bool) -> NetworkProviderDnsPlan:
    """Какие адреса сервера прописать. IPv6-адреса пишутся всегда: без IPv6 они не мешают."""
    ipv4 = normalize_dns_list(data.get("ipv4", []))
    ipv6 = normalize_dns_list(data.get("ipv6", []))
    if not ipv4 and not (ipv6 and ipv6_available):
        return NetworkProviderDnsPlan(
            valid=False,
            ipv4=[],
            ipv6=[],
            log_level="WARNING",
            log_message=f"DNS: у провайдера {name} нет DNS адресов для текущей системы",
        )
    return NetworkProviderDnsPlan(valid=True, ipv4=ipv4, ipv6=ipv6, log_level=None, log_message="")


def _apply_result(what: str, *, adapter_count: int, success_count: int, error: str) -> NetworkDnsApplyResultPlan:
    done = adapter_count > 0 and success_count == adapter_count
    return NetworkDnsApplyResultPlan(
        log_level="INFO" if done else None,
        log_message=f"DNS: {what} применён к адаптерам: {success_count}" if done else "",
        adapter_count=int(adapter_count),
        success_count=int(success_count),
        error=str(error or ""),
    )


def build_auto_dns_apply_result_plan(*, adapter_count: int, success_count: int, error: str = "") -> NetworkDnsApplyResultPlan:
    return _apply_result("автоматический DNS", adapter_count=adapter_count, success_count=success_count, error=error)


def build_provider_dns_apply_result_plan(
    *,
    name: str,
    adapter_count: int,
    success_count: int,
    ipv6: list[str],
    error: str = "",
) -> NetworkDnsApplyResultPlan:
    what = f"{name} (IPv4+IPv6)" if ipv6 else name
    return _apply_result(what, adapter_count=adapter_count, success_count=success_count, error=error)


def build_flush_dns_cache_result_plan(*, success: bool, message: str, language: str = "ru") -> NetworkFlushDnsCacheResultPlan:
    if success:
        return NetworkFlushDnsCacheResultPlan(success=True, infobar_level=None, title="", content="")
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


# ── совет про DNS провайдера ──────────────────────────────────────────────


def should_show_isp_dns_warning(adapters, *, warning_already_shown: bool) -> bool:
    """Показываем один раз, если на всех подключённых адаптерах DNS автоматический."""
    if warning_already_shown:
        return False
    connected = [adapter for adapter in adapters if adapter.connected]
    return bool(connected) and all(adapter.is_automatic for adapter in connected)


def build_isp_dns_warning_plan(adapters, *, warning_already_shown: bool, language: str = "ru") -> NetworkIspDnsWarningPlan:
    return NetworkIspDnsWarningPlan(
        should_show=should_show_isp_dns_warning(adapters, warning_already_shown=warning_already_shown),
        title=tr_catalog("page.network.isp_dns.infobar.title", language=language, default="DNS от провайдера"),
        content=tr_catalog(
            "page.network.isp_dns.infobar.content",
            language=language,
            default=(
                "У вас установлен DNS от провайдера (получен автоматически через DHCP). "
                "Провайдерский DNS может подменять ответы и мешать обходу блокировок.\n\n"
                "Можно вручную применить публичный DNS Quad9 или выбрать другой DNS из списка ниже."
            ),
        ),
        action_text=tr_catalog("page.network.isp_dns.infobar.action", language=language, default="Установить рекомендуемый DNS"),
        dismiss_text=tr_catalog("page.network.isp_dns.infobar.dismiss", language=language, default="Нет, спасибо"),
    )


# ── текущий DNS отмеченных адаптеров ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CurrentDnsPlan:
    """Что сейчас стоит на отмеченных адаптерах.

    kind: "none" — адаптеры не отмечены; "auto" — DNS получается
    автоматически (ipv4/ipv6 — адреса, выданные роутером); "provider" — один
    из известных серверов (provider — его имя); "custom" — адреса, которых
    нет в списке; "mixed" — на отмеченных адаптерах стоят разные DNS.
    """

    kind: str
    provider: str | None = None
    ipv4: tuple[str, ...] = ()
    ipv6: tuple[str, ...] = ()


def find_provider_for_dns(providers: dict, ipv4, ipv6) -> str | None:
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


def describe_adapter_dns(adapter, providers: dict) -> CurrentDnsPlan:
    if adapter.is_automatic:
        return CurrentDnsPlan(kind="auto", ipv4=tuple(adapter.auto_ipv4), ipv6=tuple(adapter.auto_ipv6))
    provider = find_provider_for_dns(providers, adapter.static_ipv4, adapter.static_ipv6)
    return CurrentDnsPlan(
        kind="provider" if provider else "custom",
        provider=provider,
        ipv4=tuple(adapter.static_ipv4),
        ipv6=tuple(adapter.static_ipv6),
    )


def build_current_dns_plan(*, adapters, providers: dict) -> CurrentDnsPlan:
    plans = [describe_adapter_dns(adapter, providers) for adapter in adapters]
    if not plans:
        return CurrentDnsPlan(kind="none")
    first = plans[0]

    def same(plan: CurrentDnsPlan) -> bool:
        if plan.kind != first.kind:
            return False
        if plan.kind == "auto":
            return True
        return (plan.provider, plan.ipv4[:1], plan.ipv6[:1]) == (first.provider, first.ipv4[:1], first.ipv6[:1])

    return first if all(same(plan) for plan in plans[1:]) else CurrentDnsPlan(kind="mixed")
