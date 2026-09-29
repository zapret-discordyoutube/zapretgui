"""DNS-слой программы: состояние адаптеров и действия над ними.

Цепочка: dns.winapi (вызовы Windows) → dns.adapters (какие адаптеры наши)
→ этот модуль (что сделать) → dns.commands (публичные команды для фасада).

Адаптеры опознаются по GUID. Всё здесь блокирующее и вызывается только из
фоновых воркеров.
"""

from __future__ import annotations

from threading import RLock

from dns import winapi
from dns.adapters import DnsAdapter, build_dns_adapters, is_dns_adapter
from dns.dns_providers import doh_templates
from dns.state import DnsCommandResult, DnsState
from log.log import log

_warmed_state: DnsState | None = None
_warmed_lock = RLock()


# ── состояние ─────────────────────────────────────────────────────────────


def load_state() -> DnsState:
    """Адаптеры для страницы DNS с их текущими DNS, наличие IPv6 и DoH."""
    interfaces = winapi.list_interfaces()
    route = winapi.internet_route()
    static = {}
    for interface in interfaces:
        if not is_dns_adapter(interface, route):
            continue
        try:
            static[interface.guid] = winapi.read_static_dns(interface.guid)
        except winapi.DnsWinApiError as exc:
            log(f"DNS: не удалось прочитать DNS адаптера «{interface.name}»: {exc}", "WARNING")
    return DnsState(
        adapters=build_dns_adapters(interfaces, route, static),
        ipv6_available=route.has_ipv6,
        doh_supported=winapi.is_doh_supported(),
    )


def warm_state() -> DnsState:
    """Загружает состояние заранее (после запуска), чтобы страница открылась сразу."""
    global _warmed_state
    state = load_state()
    with _warmed_lock:
        _warmed_state = state
    return state


def consume_warmed_state() -> DnsState | None:
    """Отдаёт заранее загруженное состояние один раз."""
    global _warmed_state
    with _warmed_lock:
        state, _warmed_state = _warmed_state, None
    return state


# ── действия ──────────────────────────────────────────────────────────────


def apply_dns(guids: list[str], ipv4: list[str], ipv6: list[str]) -> DnsCommandResult:
    """Прописывает DNS отмеченным адаптерам; пустые списки — вернуть автоматические.

    Обе версии протокола пишутся всегда: если у сервера нет IPv6-адресов,
    IPv6 возвращается в автоматический режим, чтобы на адаптере не осталось
    IPv6-адресов прежнего сервера.

    Пишем только адаптерам, которые сейчас есть на странице: Windows молча
    принимает любой GUID и заводит под него ветку реестра, поэтому
    исчезнувший адаптер надо отсечь до записи.
    """
    guids = list(dict.fromkeys(str(item or "").strip() for item in guids if str(item or "").strip()))
    names = {adapter.guid: adapter.name for adapter in load_state().adapters}
    templates = doh_templates() if winapi.is_doh_supported() else None
    errors: list[str] = []
    for guid in guids:
        if guid not in names:
            errors.append(f"адаптер {guid} не найден — он отключён или удалён")
            continue
        try:
            winapi.write_dns(guid, ipv4, ipv6=False, doh_templates=templates)
            winapi.write_dns(guid, ipv6, ipv6=True, doh_templates=templates)
        except (winapi.DnsWinApiError, ValueError) as exc:
            errors.append(f"«{names[guid]}»: {exc}")
    if len(errors) < len(guids):
        winapi.flush_resolver_cache()
    message = "; ".join(errors)
    if message:
        log(f"DNS: {message}", "WARNING")
    return DnsCommandResult(
        success=not errors,
        message=message,
        affected_count=len(guids) - len(errors),
        total_count=len(guids),
    )


def reset_to_auto(guids: list[str]) -> DnsCommandResult:
    return apply_dns(guids, [], [])


def flush_dns_cache() -> DnsCommandResult:
    if winapi.flush_resolver_cache():
        return DnsCommandResult(success=True)
    return DnsCommandResult(success=False, message="Windows не смогла очистить кэш DNS")


def adapters_with_static_dns() -> tuple[DnsAdapter, ...]:
    return tuple(adapter for adapter in load_state().adapters if not adapter.is_automatic)


__all__ = [
    "adapters_with_static_dns",
    "apply_dns",
    "consume_warmed_state",
    "flush_dns_cache",
    "load_state",
    "reset_to_auto",
    "warm_state",
]
