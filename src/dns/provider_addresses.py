"""Адреса чужих DNS-серверов, которые программа узнаёт сама, без правки кода.

Поставщики меняют адреса (Malw.link заменил их 2026-10-20), а встроенный в программу список
верен на день выпуска. Для серверов, у которых адреса плитки — это адреса их имени DoH
(``"addresses_by_name": True`` в ``dns.dns_providers``: Malw.link, AstraCat), программа раз в
сутки спрашивает это имя и проверяет каждый найденный адрес настоящим запросом DoH с проверкой
сертификата этого имени (``dns.doh_lookup.find_doh_addresses``): подменённый провайдером или чужой
адрес сертификат сервера не предъявит. Проверенные адреса хранятся в настройках
(``dns.learned_servers``) и с этого момента — нынешние для плитки; адаптеры, которые смотрят на
прежний адрес, переводятся на нынешний (``dns.address_migration``).

Узнаётся это двумя путями, и они дополняют друг друга. При сборке (``learn_baseline``) выпуск сам спрашивает
имена и кладёт проверенные адреса в файл ``system/dns_provider_addresses.json`` — это адреса «на день
установки», код для них править не нужно. А на компьютере человека программа спрашивает то же имя в
момент нажатия плитки (``refresh_for_apply``), при запуске и раз в сутки: новее всегда то, что узнано позже.

Это то же, что делает ``dns.zapret_dns_server`` для своего сервера проекта, только для списка чужих.
Имя не ответило или ни один адрес не прошёл проверку — остаются прежние: неудачный вопрос
никогда не меняет сервер.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass

from log.log import log

__all__ = [
    "baseline_json",
    "learn_baseline",
    "read_baseline",
    "FLAG",
    "ProviderChange",
    "adopt",
    "address_replacements",
    "current_ipv4",
    "refresh",
    "tracked",
]

FLAG = "addresses_by_name"
# Сколько адресов одного сервера хранить и сколько прежних помнить, чтобы перевести с них адаптеры.
MAX_ADDRESSES = 4
MAX_FORMER = 8

# Встроенные в код адреса плиток: ``adopt`` меняет список плитки на месте, поэтому
# встроенное запоминается до первой замены.
_SHIPPED: dict[str, tuple[str, ...]] = {}
# Адреса из файла сборки (system/dns_provider_addresses.json); None — файл ещё не читали.
_BASELINE: dict[str, tuple[str, ...]] | None = None


@dataclass(frozen=True, slots=True)
class ProviderChange:
    """Итог вопроса об адресе одного сервера: changed — нынешние адреса стали другими."""

    name: str
    ipv4: tuple[str, ...]
    changed: bool
    detail: str = ""


def _global_ipv4(values) -> tuple[str, ...]:
    result: list[str] = []
    for value in values or ():
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError:
            continue
        text = address.compressed
        if address.version == 4 and address.is_global and text not in result:
            result.append(text)
    return tuple(result)


def _stored() -> dict:
    from settings import store

    return store.get_dns_learned_servers()


def _save(name: str, *, ipv4, former_ipv4, checked_at: float) -> None:
    from settings import store

    store.set_dns_learned_server(
        name, ipv4=list(ipv4), former_ipv4=list(former_ipv4), checked_at=float(checked_at)
    )


def tracked() -> list[tuple[str, dict]]:
    """(название, запись плитки) серверов, адреса которых узнаются по имени DoH."""
    from dns.dns_providers import iter_providers

    return [
        (name, data)
        for _group, name, data in iter_providers()
        if data.get(FLAG) and data.get("doh") and not data.get("local_proxy")
    ]


def _shipped(name: str, data: dict) -> tuple[str, ...]:
    """Адреса, вшитые в код."""
    return _SHIPPED.setdefault(name, tuple(str(item) for item in data.get("ipv4") or ()))


def read_baseline(path=None) -> dict[str, tuple[str, ...]]:
    """Адреса из файла сборки: {название: IPv4}. Нет файла или он испорчен — пусто."""
    import json

    if path is None:
        from config.runtime_layout import APPLICATION_PATHS

        path = APPLICATION_PATHS.provider_addresses_file
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for name, values in (raw.items() if isinstance(raw, dict) else ()):
        addresses = _global_ipv4(values if isinstance(values, list) else ())[:MAX_ADDRESSES]
        if addresses:
            result[str(name)] = addresses
    return result


def _baseline(name: str) -> tuple[str, ...]:
    global _BASELINE
    if _BASELINE is None:
        _BASELINE = read_baseline()
    return _BASELINE.get(name, ())


def current_ipv4(name: str, data: dict, stored: dict | None = None) -> tuple[str, ...]:
    """Адреса IPv4 сервера сейчас: узнанные на компьютере, иначе из файла сборки, иначе вшитые в код."""
    shipped = _shipped(name, data)
    learned = _global_ipv4(((stored if stored is not None else _stored()).get(name) or {}).get("ipv4"))
    return learned or _baseline(name) or shipped


def address_replacements(stored: dict | None = None) -> dict[str, str]:
    """Пары «прежний адрес сервера → нынешний» для адаптеров."""
    stored = stored if stored is not None else _stored()
    pairs: dict[str, str] = {}
    for name, data in tracked():
        current = current_ipv4(name, data, stored)
        if not current:
            continue
        known = (
            *_shipped(name, data),
            *_baseline(name),
            *_global_ipv4((stored.get(name) or {}).get("former_ipv4")),
        )
        for address in known:
            if address not in current:
                pairs[address] = current[0]
    return pairs


def adopt(stored: dict | None = None) -> dict[str, tuple[str, ...]]:
    """Ставит нынешние адреса в записи плиток и сбрасывает кэш каталога hosts, если что-то изменилось.

    Список адресов плитки меняется на месте, поэтому все, кто читает каталог провайдеров, видят новое.
    Вызывается до замены старых адресов на адаптерах — и при запуске, и после вопроса об адресе.
    """
    from dns.dns_providers import iter_providers

    stored = stored if stored is not None else _stored()
    # Первый адрес плитки — её ключ: он не должен совпасть с адресом другого сервера.
    taken = {
        str(item)
        for _group, name, data in iter_providers()
        if not data.get(FLAG)
        for item in data.get("ipv4") or ()
    }
    changed: dict[str, tuple[str, ...]] = {}
    for name, data in tracked():
        addresses = tuple(address for address in current_ipv4(name, data, stored) if address not in taken)
        if addresses and tuple(data.get("ipv4") or ()) != addresses:
            data["ipv4"][:] = list(addresses)
            changed[name] = addresses
    if changed:
        from hosts.proxy_domains import invalidate_hosts_catalog_cache

        invalidate_hosts_catalog_cache()
    return changed


def _refresh_one(name: str, data: dict, stored: dict, *, cancel, lookup, now: float | None) -> ProviderChange:
    """Один сервер: спросить имя DoH, проверить адреса, записать, если они другие."""
    from dns.custom_servers import parse_doh_template

    current = current_ipv4(name, data, stored)
    try:
        template = parse_doh_template(str(data.get("doh") or ""))
        found = lookup(template, ipv6=False, doh_supported=True, cancel=cancel)
    except Exception as exc:
        return ProviderChange(name, current, False, f"вопрос не удался: {exc}")
    learned = _global_ipv4(getattr(found, "ipv4", ()))[:MAX_ADDRESSES]
    if not learned:
        reason = str(getattr(found, "error", "") or "нет проверенного адреса")
        return ProviderChange(name, current, False, reason)
    moved = set(learned) != set(current)
    if moved:
        former = _global_ipv4((stored.get(name) or {}).get("former_ipv4"))
        former = tuple(address for address in dict.fromkeys((*current, *former)) if address not in learned)
        _save(name, ipv4=learned, former_ipv4=former[:MAX_FORMER], checked_at=time.time() if now is None else now)
        log(f"DNS: сервер {name} сменил адреса: {', '.join(current)} -> {', '.join(learned)}", "INFO")
    return ProviderChange(name, learned, moved, "адреса сменились" if moved else "адреса не изменились")


def refresh(*, names=None, cancel=None, lookup=None, now: float | None = None) -> list[ProviderChange]:
    """Спрашивает адреса имён DoH отслеживаемых серверов; проверенные и другие делает нынешними.

    ``names`` — только эти серверы (по умолчанию все отслеживаемые).
    Ходит в сеть: вызывать только с пропуском шлюза проб (``utils.probe_gate``) или по нажатию человека.
    """
    if lookup is None:
        from dns.doh_lookup import find_doh_addresses

        lookup = find_doh_addresses
    wanted = None if names is None else set(names)
    results: list[ProviderChange] = []
    stored = _stored()
    for name, data in tracked():
        if wanted is not None and name not in wanted:
            continue
        results.append(_refresh_one(name, data, stored, cancel=cancel, lookup=lookup, now=now))
    if any(item.changed for item in results):
        adopt(_stored())
    return results


# Сколько секунд ждать ответа имени, когда человек нажал плитку: дольше он ждать не должен.
APPLY_LOOKUP_LIMIT_S = 8.0


def refresh_for_apply(name: str, *, limit_s: float = APPLY_LOOKUP_LIMIT_S, lookup=None) -> tuple[str, ...]:
    """Свежие адреса сервера ``name`` в момент нажатия плитки; пусто — сервер не отслеживается.

    Спрашивает имя DoH и проверяет адреса прямо сейчас, не дожидаясь суточной проверки. Не ответило за
    ``limit_s`` секунд — остаются прежние (узнанные или встроенные): нажатие не должно зависать.
    """
    import threading

    from utils.socket_cancel import SocketCancel

    if not any(item_name == name for item_name, _data in tracked()):
        return ()
    cancel = SocketCancel()
    timer = threading.Timer(max(0.1, float(limit_s)), cancel.cancel)
    timer.daemon = True
    timer.start()
    try:
        changes = refresh(names=[name], cancel=cancel, lookup=lookup)
    except Exception as exc:
        log(f"DNS: свежие адреса {name} узнать не вышло: {exc}", "WARNING")
        changes = []
    finally:
        timer.cancel()
    for item in changes:
        if item.name == name:
            return item.ipv4
    for item_name, data in tracked():
        if item_name == name:
            return current_ipv4(item_name, data)
    return ()


def learn_baseline(*, cancel=None, lookup=None) -> dict[str, list[str]]:
    """При сборке: {название: проверенные адреса} для отслеживаемых серверов, чьё имя ответило.

    Ничего не пишет в настройки: итог кладёт в файл сборка (``baseline_json``). Сервер, имя которого
    с машины сборки не ответило (закрыт из этой страны), в итог не входит — у него остаются адреса из кода.
    """
    from dns.custom_servers import parse_doh_template

    if lookup is None:
        from dns.doh_lookup import find_doh_addresses

        lookup = find_doh_addresses
    result: dict[str, list[str]] = {}
    for name, data in tracked():
        try:
            found = lookup(parse_doh_template(str(data.get("doh") or "")), ipv6=False, doh_supported=True, cancel=cancel)
        except Exception:
            continue
        learned = _global_ipv4(getattr(found, "ipv4", ()))[:MAX_ADDRESSES]
        if learned:
            result[name] = list(learned)
    return result


def baseline_json(mapping: dict[str, list[str]]) -> str:
    import json

    return json.dumps({name: list(values) for name, values in sorted(mapping.items())}, ensure_ascii=False, indent=2) + "\n"
