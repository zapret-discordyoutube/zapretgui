"""DNS-слой программы: состояние адаптеров и действия над ними.

Цепочка: dns.winapi (вызовы Windows) → dns.adapters (какие адаптеры наши)
→ этот модуль (что сделать) → dns.commands (публичные команды для фасада).

Адаптеры опознаются по GUID. Всё здесь блокирующее и вызывается только из
фоновых воркеров.
"""

from __future__ import annotations

from dataclasses import replace
from threading import RLock

from dns import winapi
from dns.adapters import DnsAdapter, build_dns_adapters, is_dns_adapter
from dns import doh_switch
from dns.custom_servers import custom_doh_templates
from dns.dns_providers import doh_templates
from dns.reachability import check_servers
from dns.state import DnsCommandResult, DnsState
from dns.step_log import timed_step
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
        doh_enabled=doh_switch.is_enabled(),
        local_proxy_mode=_local_proxy_mode(),
        local_proxy_server=_local_proxy_server(),
        custom_servers=tuple(load_custom_servers()),
    )


def load_custom_servers() -> list[dict]:
    """Свои DNS-серверы пользователя из настроек; при сбое чтения — пустой список."""
    try:
        from settings.store import get_custom_dns_servers

        return list(get_custom_dns_servers())
    except Exception as exc:
        log(f"DNS: не удалось прочитать свои DNS-серверы: {exc}", "WARNING")
        return []


def write_doh_templates() -> dict[str, str] | None:
    """{адрес: шаблон DoH} для записи в адаптер или None, если Windows DoH не умеет.

    Шаблоны серверов программы и своих серверов пользователя; при совпадении
    адреса главнее сервер программы.

    Переключатель «Шифровать DNS (DoH)» выключен: пустой словарь. Windows при
    записи тогда снимает с адаптера прежние ссылки DoH, а не оставляет их
    (None этого не делает: поля DoH в запись не попадают).
    """
    if not winapi.is_doh_supported():
        return None
    if not doh_switch.is_enabled():
        return {}
    return {**custom_doh_templates(load_custom_servers()), **doh_templates()}


def _local_proxy_mode() -> str:
    try:
        from dns import local_proxy

        return local_proxy.active_mode()
    except Exception as exc:
        log(f"DNS: не удалось узнать режим шифрованного DNS: {exc}", "DEBUG")
        return ""


def _local_proxy_server() -> str:
    try:
        from dns import local_proxy

        return local_proxy.active_server()
    except Exception as exc:
        log(f"DNS: не удалось узнать сервер шифрованного DNS: {exc}", "DEBUG")
        return ""


def load_page_state() -> DnsState:
    """Состояние для страницы «Настройки DNS»: то же плюс прошлая проверка серверов на этой линии."""
    from dns.line_check import load_line_check

    return replace(load_state(), line_check=load_line_check())


def warm_state() -> DnsState:
    """Загружает состояние заранее (после запуска), чтобы страница открылась сразу."""
    global _warmed_state
    state = load_page_state()
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

    Сервер, который из сети пользователя не отвечает ни одним способом, не
    ставится вовсе (dns.reachability): его мог заблокировать провайдер, и с
    ним компьютер остался бы без DNS.
    """
    guids = list(dict.fromkeys(str(item or "").strip() for item in guids if str(item or "").strip()))
    with timed_step("чтение адаптеров"):
        names = {adapter.guid: adapter.name for adapter in load_state().adapters}
    with timed_step("шаблоны шифрования Windows"):
        templates = write_doh_templates()
    with timed_step(f"проверка сервера {', '.join(ipv4 or ipv6 or []) or 'автоматический'}"):
        reach = check_servers(list(ipv4 or []), list(ipv6 or []), templates)
    if reach.blocked:
        log(f"DNS: {reach.blocked}", "WARNING")
        return DnsCommandResult(success=False, message=reach.blocked, affected_count=0, total_count=len(guids))
    errors: list[str] = []
    for guid in guids:
        if guid not in names:
            errors.append(f"адаптер {guid} не найден — он отключён или удалён")
            continue
        try:
            with timed_step(f"запись DNS адаптеру «{names[guid]}»"):
                winapi.write_dns(guid, ipv4, ipv6=False, doh_templates=templates)
                winapi.write_dns(guid, ipv6, ipv6=True, doh_templates=templates)
        except (winapi.DnsWinApiError, ValueError) as exc:
            errors.append(f"«{names[guid]}»: {exc}")
    if len(errors) < len(guids):
        with timed_step("очистка кэша DNS"):
            winapi.flush_resolver_cache()
    message = "; ".join(errors)
    if message:
        log(f"DNS: {message}", "WARNING")
    affected = len(guids) - len(errors)
    if reach.doh_closed and affected:
        log("DNS: шифрованный путь (DoH) до сервера закрыт — Windows спрашивает его обычными запросами", "WARNING")
    return DnsCommandResult(
        success=not errors,
        message=message,
        affected_count=affected,
        total_count=len(guids),
        doh_closed=reach.doh_closed and affected > 0,
    )


def reset_to_auto(guids: list[str]) -> DnsCommandResult:
    return apply_dns(guids, [], [])


def flush_dns_cache() -> DnsCommandResult:
    if winapi.flush_resolver_cache():
        return DnsCommandResult(success=True)
    return DnsCommandResult(success=False, message="Windows не смогла очистить кэш DNS")


# ── шифрованный DNS (встроенный dnscrypt-proxy) ───────────────────────────


def _static_addresses() -> list[str]:
    """Все адреса DNS, прописанные вручную на адаптерах страницы."""
    return [
        address
        for adapter in load_state().adapters
        for address in (*adapter.static_ipv4, *adapter.static_ipv6)
    ]


def start_local_proxy(mode: str, *, upstreams=(), server_name: str = "") -> DnsCommandResult:
    """Запускает шифрованный DNS; адаптеры не трогает — их пишет apply_dns после успеха.

    upstreams и server_name — серверы и название плитки для режима DoH.
    """
    from dns import local_proxy

    result = local_proxy.start(mode, upstreams=tuple(upstreams), server_name=server_name)
    if not result.success:
        log(f"DNS: шифрованный DNS ({mode}) не запущен: {result.message}", "WARNING")
    return DnsCommandResult(success=result.success, message=result.message, listen_ipv6=result.listen_ipv6)


def stop_local_proxy_if_unused() -> bool:
    """После смены DNS: движок убирается, если на 127.0.0.1 больше никто не смотрит."""
    from dns import local_proxy

    return local_proxy.stop_if_unused(_static_addresses())


def repair_local_proxy() -> str:
    """Проверка шифрованного DNS при запуске программы (см. local_proxy.repair)."""
    from dns import local_proxy

    summary = local_proxy.repair(_static_addresses())
    if summary:
        log(f"DNS: {summary}", "INFO")
        winapi.flush_resolver_cache()
    return summary


def adapters_with_static_dns() -> tuple[DnsAdapter, ...]:
    return tuple(adapter for adapter in load_state().adapters if not adapter.is_automatic)


__all__ = [
    "adapters_with_static_dns",
    "apply_dns",
    "consume_warmed_state",
    "flush_dns_cache",
    "load_custom_servers",
    "load_page_state",
    "load_state",
    "repair_local_proxy",
    "reset_to_auto",
    "start_local_proxy",
    "stop_local_proxy_if_unused",
    "warm_state",
    "write_doh_templates",
]
