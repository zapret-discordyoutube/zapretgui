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
    # DNS встал, но шифрование до сервера не доходит (DnsCommandResult.doh_closed).
    doh_closed: bool = False
    # Причина «без шифрования» — ответ самого сервера на пробный вопрос движка, а не закрытая сеть.
    engine_refused: bool = False


@dataclass(slots=True)
class NetworkFlushDnsCacheResultPlan:
    success: bool
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
    doh_closed: bool = False,
    engine_refused: bool = False,
) -> NetworkDnsApplyResultPlan:
    what = f"{name} (IPv4+IPv6)" if ipv6 else name
    plan = _apply_result(what, adapter_count=adapter_count, success_count=success_count, error=error)
    plan.doh_closed = bool(doh_closed)
    plan.engine_refused = bool(engine_refused) and plan.doh_closed
    return plan


def build_flush_dns_cache_result_plan(*, success: bool, message: str, language: str = "ru") -> NetworkFlushDnsCacheResultPlan:
    if success:
        return NetworkFlushDnsCacheResultPlan(success=True, title="", content="")
    return NetworkFlushDnsCacheResultPlan(
        success=False,
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


LOCAL_PROXY_ADDRESSES = frozenset({"127.0.0.1", "::1"})


def find_provider_for_dns(
    providers: dict, ipv4, ipv6, local_proxy_mode: str = "", local_proxy_server: str = ""
) -> str | None:
    """Имя сервера из списка, чей основной адрес стоит первым на адаптере.

    Адрес 127.0.0.1 — встроенный шифрованный DNS: у всех его режимов адрес
    один, поэтому плитку называет режим работающего движка (local_proxy_mode).
    В режиме DoH движок работает на сервер обычной плитки — её называет
    local_proxy_server. Движок не работает — это чей-то чужой локальный DNS.
    """
    first = (list(ipv4) or list(ipv6) or [""])[0]
    if first in LOCAL_PROXY_ADDRESSES:
        if local_proxy_server:
            return next((name for group in providers.values() for name in group if name == local_proxy_server), None)
        for group in providers.values():
            for name, data in group.items():
                if local_proxy_mode and data.get("local_proxy") == local_proxy_mode:
                    return name
        return None
    for group in providers.values():
        for name, data in group.items():
            if data.get("local_proxy"):
                continue
            provider_v4 = normalize_dns_list(data.get("ipv4", []))
            provider_v6 = normalize_dns_list(data.get("ipv6", []))
            if ipv4 and provider_v4 and provider_v4[0] == ipv4[0]:
                return name
            if not ipv4 and ipv6 and provider_v6 and provider_v6[0] == ipv6[0]:
                return name
    return None


def describe_adapter_dns(
    adapter, providers: dict, local_proxy_mode: str = "", local_proxy_server: str = ""
) -> CurrentDnsPlan:
    if adapter.is_automatic:
        return CurrentDnsPlan(kind="auto", ipv4=tuple(adapter.auto_ipv4), ipv6=tuple(adapter.auto_ipv6))
    provider = find_provider_for_dns(
        providers, adapter.static_ipv4, adapter.static_ipv6, local_proxy_mode, local_proxy_server
    )
    return CurrentDnsPlan(
        kind="provider" if provider else "custom",
        provider=provider,
        ipv4=tuple(adapter.static_ipv4),
        ipv6=tuple(adapter.static_ipv6),
    )


LEFTOVER_IPV6 = "ipv6"
LEFTOVER_ADAPTER = "adapter"


@dataclass(frozen=True, slots=True)
class DnsLeftover:
    """Дорога, по которой DNS-запросы идут мимо выбранного сервера."""

    kind: str  # LEFTOVER_IPV6 | LEFTOVER_ADAPTER
    adapter: str
    addresses: tuple[str, ...]


def _real_dns(addresses) -> tuple[str, ...]:
    """Адреса DNS без заглушек Windows: fec0:0:0:ffff::1…3 она показывает там, где IPv6-DNS нет вовсе."""
    return tuple(item for item in addresses if not str(item).lower().startswith("fec0:"))


def find_dns_leftovers(adapters, selected_guids) -> tuple[DnsLeftover, ...]:
    """Что осталось мимо выбранного сервера после его установки. Пусто — выбранный сервер один на всех дорогах.

    Windows спрашивает все DNS-серверы, которые у неё есть, и берёт первый ответ:

    - у адаптера с выбранным сервером по IPv4 может остаться автоматический DNS по
      IPv6 (от роутера или провайдера) — так бывает, когда у выбранного сервера
      адресов IPv6 нет; Windows спрашивает его наравне с выбранным;
    - на подключённом, но не отмеченном адаптере остаётся его собственный DNS.

    Пока сервер не выбран («Автоматически»), утечки нет: выбирать не из чего.
    """
    selected = set(selected_guids)
    chosen = [item for item in adapters if item.guid in selected and item.connected and (item.static_ipv4 or item.static_ipv6)]
    if not chosen:
        return ()
    found: list[DnsLeftover] = []
    for adapter in chosen:
        automatic_v6 = _real_dns(adapter.auto_ipv6)
        if adapter.static_ipv4 and not adapter.static_ipv6 and automatic_v6:
            found.append(DnsLeftover(LEFTOVER_IPV6, adapter.name, automatic_v6))
    primary = {(adapter.static_ipv4 or adapter.static_ipv6)[0] for adapter in chosen}
    for adapter in adapters:
        if adapter.guid in selected or not adapter.connected:
            continue
        own = _real_dns((*adapter.static_ipv4, *adapter.static_ipv6)) or _real_dns((*adapter.auto_ipv4, *adapter.auto_ipv6))
        if own and own[0] not in primary:
            found.append(DnsLeftover(LEFTOVER_ADAPTER, adapter.name, own))
    return tuple(found)


def build_current_dns_plan(
    *, adapters, providers: dict, local_proxy_mode: str = "", local_proxy_server: str = ""
) -> CurrentDnsPlan:
    plans = [describe_adapter_dns(adapter, providers, local_proxy_mode, local_proxy_server) for adapter in adapters]
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
