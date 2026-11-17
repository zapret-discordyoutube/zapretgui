"""Адреса чужих DNS-серверов, которые программа узнаёт и проверяет сама, без правки кода.

Поставщики меняют адреса (Malw.link заменил их 2026-10-20), а встроенный в программу список
верен на день выпуска. Поэтому для КАЖДОГО сервера с адресом DoH (все плитки, кроме режимов встроенного
движка и собственного сервера проекта — тот узнаёт адрес сам, ``dns.zapret_dns_server``) программа:

1. спрашивает имя DoH сервера и проверяет каждый найденный адрес настоящим запросом DoH с проверкой
   сертификата этого имени (``dns.doh_lookup.find_doh_addresses``): подменённый провайдером или чужой
   адрес сертификат сервера не предъявит. В список плитки они попадают, если это тот же сервис
   ("addresses_by_name": у части поставщиков одно имя DoH обслуживает разные фильтры, и чужой фильтр
   человеку не нужен) или если ни один известный адрес не жив — тогда менять нужно в любом случае;
2. проверяет заново и уже известные адреса — встроенные в код, найденные раньше, из файла сборки:
   обычным запросом DNS и запросом DoH. Мёртвый адрес из списка уходит, живой остаётся, новый
   (проверенный) добавляется. Адреса, где DoH отвечает, идут вперёд — их берёт шифрованный DNS;
3. ничего не меняет, если не прошёл проверку ни один адрес (нет сети, сервер закрыт на этой линии):
   неудачный вопрос никогда не меняет сервер.

Итог хранится в настройках (``dns.learned_servers``) и с этого момента — нынешний для плитки; адаптеры,
которые смотрят на ушедший адрес, переводятся на нынешний (``dns.address_migration``). Адреса, где
отвечает только DoH, в список обычного DNS не попадают: плитку ставят адресами и в обычном режиме, а
для шифрованного DNS движка такие адреса берутся из имени прямо при применении (``dns.doh_name_addresses``).

Узнаётся это двумя путями, и они дополняют друг друга. При сборке (``learn_baseline``) выпуск сам спрашивает
имена и кладёт проверенные адреса в файл ``system/dns_provider_addresses.json``: это «запасные на день
установки», они идут после встроенных и ничего не вытесняют (машина сборки стоит в другой стране, и
закрытый ей адрес может работать у человека). А на компьютере человека программа делает полную проверку
в момент нажатия плитки (``refresh_for_apply``), при запуске и раз в сутки: новее и точнее то, что узнано позже
и на его линии.
"""

from __future__ import annotations

import ipaddress
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from log.log import log

__all__ = [
    "ProviderChange",
    "adopt",
    "address_replacements",
    "baseline_json",
    "current_ipv4",
    "is_tracked",
    "learn_baseline",
    "read_baseline",
    "refresh",
    "refresh_for_apply",
    "tracked",
]

# Сколько адресов одного сервера хранить и сколько прежних помнить, чтобы перевести с них адаптеры.
MAX_ADDRESSES = 4
MAX_FORMER = 8
# Сколько серверов спрашивать одновременно при суточной проверке и при сборке.
PARALLEL_SERVERS = 4
# Сколько секунд после удачной проверки сервера нажатие плитки её не повторяет: быстрое применение важнее.
APPLY_FRESH_FOR_S = 600.0

# Встроенные в код адреса плиток: ``adopt`` меняет список плитки на месте, поэтому
# встроенное запоминается до первой замены.
_SHIPPED: dict[str, tuple[str, ...]] = {}
# Адреса из файла сборки (system/dns_provider_addresses.json); None — файл ещё не читали.
_BASELINE: dict[str, tuple[str, ...]] | None = None
# Когда (по часам процесса) сервер последний раз удачно проверялся: название -> time.monotonic().
_CHECKED: dict[str, float] = {}


@dataclass(frozen=True, slots=True)
class ProviderChange:
    """Итог проверки адресов одного сервера: changed — нынешние адреса стали другими."""

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


def is_tracked(name: str, data: dict) -> bool:
    """Узнаются ли адреса сервера по имени DoH: у него есть адрес DoH и адреса IPv4, это не режим движка и не свой сервер."""
    from dns.dns_providers import ZAPRET_DNS_PROVIDER_NAME

    return bool(
        name != ZAPRET_DNS_PROVIDER_NAME
        and data.get("doh")
        and data.get("ipv4")
        and not data.get("local_proxy")
    )


def tracked() -> list[tuple[str, dict]]:
    """(название, запись плитки) серверов, адреса которых узнаются по имени DoH: все с адресом DoH."""
    from dns.dns_providers import iter_providers

    return [(name, data) for _group, name, data in iter_providers() if is_tracked(name, data)]


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
    """Адреса IPv4 сервера сейчас.

    Проверенные на этом компьютере — как есть. Пока проверки не было: встроенные в код, а после них
    запасные из файла сборки (они ничего не вытесняют: машина сборки видит сеть иначе).
    """
    shipped = _shipped(name, data)
    learned = _global_ipv4(((stored if stored is not None else _stored()).get(name) or {}).get("ipv4"))
    if learned:
        return learned[:MAX_ADDRESSES]
    return tuple(dict.fromkeys((*shipped, *_baseline(name))))[:MAX_ADDRESSES]


def address_replacements(stored: dict | None = None) -> dict[str, str]:
    """Пары «прежний адрес сервера → нынешний» для адаптеров: ушедшие из списка адреса."""
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
    Вызывается до замены старых адресов на адаптерах — и при запуске, и после проверки адресов.
    """
    from dns.dns_providers import iter_providers

    stored = stored if stored is not None else _stored()
    items = list(tracked())
    tracked_names = {name for name, _data in items}
    # Первый адрес плитки — её ключ: чужой встроенный адрес в список этой плитки попасть не должен.
    claimed: dict[str, str] = {}
    for _group, name, data in iter_providers():
        shipped = _shipped(name, data) if name in tracked_names else tuple(str(item) for item in data.get("ipv4") or ())
        for address in shipped:
            claimed.setdefault(address, name)
    changed: dict[str, tuple[str, ...]] = {}
    for name, data in items:
        addresses = tuple(
            address for address in current_ipv4(name, data, stored) if claimed.get(address, name) == name
        )
        if addresses and tuple(data.get("ipv4") or ()) != addresses:
            data["ipv4"][:] = list(addresses)
            changed[name] = addresses
    if changed:
        from hosts.proxy_domains import invalidate_hosts_catalog_cache

        invalidate_hosts_catalog_cache()
    return changed


def _answered(probe, *args) -> bool:
    try:
        return bool(probe(*args).answered)
    except Exception:
        return False


def _verify(address: str, template, *, encrypted_only: bool, cancel) -> tuple[bool, bool]:
    """(отвечает ли адрес по DoH, отвечает ли по обычному DNS): настоящие запросы, сертификат проверяется."""
    from dns.doh_lookup import probe_doh, probe_plain

    doh = _answered(probe_doh, address, template, cancel)
    # Сервер только с шифрованным DNS обычного не отдаёт: спрашивать его без толку.
    plain = False if encrypted_only else _answered(probe_plain, address, cancel)
    return doh, plain


@dataclass(frozen=True, slots=True)
class _Verdict:
    """Итог сетевой части проверки: что записать (None — ничего) и что сказать."""

    change: ProviderChange
    ipv4: tuple[str, ...] | None = None
    former: tuple[str, ...] = ()


def _check_one(name: str, data: dict, stored: dict, *, cancel, lookup, verify) -> _Verdict:
    """Один сервер: спросить имя DoH, проверить адреса — и найденные, и уже известные. Настройки не трогает."""
    from dns.custom_servers import parse_doh_template
    from dns.dns_providers import is_encrypted_only

    current = current_ipv4(name, data, stored)
    encrypted_only = is_encrypted_only(data)
    try:
        template = parse_doh_template(str(data.get("doh") or ""))
    except Exception as exc:
        return _Verdict(ProviderChange(name, current, False, f"адрес DoH не разобран: {exc}"))
    reason = ""
    by_name: tuple[str, ...] = ()
    try:
        # Адреса имени, прошедшие и DoH, и (кроме серверов только с шифрованием) обычный DNS: их можно ставить в список.
        found = lookup(template, ipv6=False, doh_supported=encrypted_only, cancel=cancel)
        by_name = _global_ipv4(getattr(found, "ipv4", ()))[:MAX_ADDRESSES]
        if not by_name:
            reason = str(getattr(found, "error", "") or "имя не дало проверенного адреса")
    except Exception as exc:
        reason = f"вопрос не удался: {exc}"

    # Кандидаты: встроенные первыми (ключ плитки не прыгает), затем найденные раньше, из сборки, из имени.
    known = tuple(
        dict.fromkeys(
            (
                *_shipped(name, data),
                *_global_ipv4((stored.get(name) or {}).get("ipv4")),
                *_baseline(name),
            )
        )
    )
    to_check = [address for address in known if address not in by_name]
    results: dict[str, tuple[bool, bool]] = {address: (True, not encrypted_only) for address in by_name}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(len(to_check), 8)) as pool:
            for address, answer in zip(
                to_check,
                pool.map(lambda item: verify(item, template, encrypted_only=encrypted_only, cancel=cancel), to_check),
            ):
                results[address] = (bool(answer[0]), bool(answer[1]))

    def is_alive(address: str) -> bool:
        # Живой адрес: отвечает по DoH (серверы только с шифрованием) или по обычному DNS (остальные).
        return results[address][0] if encrypted_only else results[address][1]

    # Адреса имени добавляются, только если это тот же сервис: у части поставщиков одно имя DoH обслуживает
    # разные фильтры (CleanBrowsing, Control D: профиль выбирает путь), и чужой фильтр человеку не нужен.
    # Верим имени там, где это проверено ("addresses_by_name"), и там, где ни один известный адрес не жив:
    # тогда менять всё равно нужно, а имя — официальный источник.
    trusted = bool(data.get("addresses_by_name")) or not any(is_alive(address) for address in known)
    order = tuple(dict.fromkeys((*known, *(by_name if trusted else ()))))
    # Где отвечает DoH — вперёд: его берёт шифрованный DNS.
    alive = [address for address in order if is_alive(address)]
    alive.sort(key=lambda address: not results[address][0])
    verified = tuple(alive)[:MAX_ADDRESSES]
    if not verified:
        return _Verdict(ProviderChange(name, current, False, reason or "ни один адрес не прошёл проверку"))
    if set(verified) == set(current):
        return _Verdict(ProviderChange(name, current, False, "адреса не изменились"), ipv4=None)
    former = _global_ipv4((stored.get(name) or {}).get("former_ipv4"))
    former = tuple(address for address in dict.fromkeys((*current, *former)) if address not in verified)
    return _Verdict(ProviderChange(name, verified, True, "адреса сменились"), ipv4=verified, former=former[:MAX_FORMER])


def refresh(*, names=None, cancel=None, lookup=None, verify=None, now: float | None = None) -> list[ProviderChange]:
    """Проверяет адреса отслеживаемых серверов; проверенные и другие делает нынешними.

    ``names`` — только эти серверы (по умолчанию все отслеживаемые).
    Ходит в сеть: вызывать только с пропуском шлюза проб (``utils.probe_gate``) или по нажатию человека.
    """
    if lookup is None:
        from dns.doh_lookup import find_doh_addresses

        lookup = find_doh_addresses
    verify = verify or _verify
    wanted = None if names is None else set(names)
    items = [(name, data) for name, data in tracked() if wanted is None or name in wanted]
    stored = _stored()

    def ask(item) -> _Verdict:
        name, data = item
        try:
            return _check_one(name, data, stored, cancel=cancel, lookup=lookup, verify=verify)
        except Exception as exc:
            return _Verdict(ProviderChange(name, current_ipv4(name, data, stored), False, f"проверка не удалась: {exc}"))

    if len(items) > 1:
        with ThreadPoolExecutor(max_workers=PARALLEL_SERVERS) as pool:
            verdicts = list(pool.map(ask, items))
    else:
        verdicts = [ask(item) for item in items]
    results: list[ProviderChange] = []
    moment = time.monotonic()
    for verdict in verdicts:
        change = verdict.change
        if verdict.ipv4 is not None:
            _save(
                change.name,
                ipv4=verdict.ipv4,
                former_ipv4=verdict.former,
                checked_at=time.time() if now is None else now,
            )
            log(f"DNS: сервер {change.name} сменил адреса: {', '.join(change.ipv4)}", "INFO")
        if change.changed or change.detail == "адреса не изменились":
            _CHECKED[change.name] = moment
        results.append(change)
    if any(item.changed for item in results):
        adopt(_stored())
    return results


# Сколько секунд ждать ответа, когда человек нажал плитку: дольше он ждать не должен.
APPLY_LOOKUP_LIMIT_S = 8.0


def refresh_for_apply(
    name: str, *, limit_s: float = APPLY_LOOKUP_LIMIT_S, lookup=None, verify=None, fresh_for_s: float = APPLY_FRESH_FOR_S
) -> tuple[str, ...]:
    """Свежие адреса сервера ``name`` в момент нажатия плитки; пусто — сервер не отслеживается.

    Проверяет адреса прямо сейчас, не дожидаясь суточной проверки. Не уложилась в ``limit_s`` секунд —
    остаются прежние (проверенные или встроенные): нажатие не должно зависать. Если сервер
    удачно проверялся меньше ``fresh_for_s`` секунд назад, повторно не спрашивает.
    """
    import threading

    from utils.socket_cancel import SocketCancel

    items = {item_name: data for item_name, data in tracked()}
    if name not in items:
        return ()
    checked = _CHECKED.get(name)
    if checked is not None and time.monotonic() - checked < fresh_for_s:
        return current_ipv4(name, items[name])
    cancel = SocketCancel()
    timer = threading.Timer(max(0.1, float(limit_s)), cancel.cancel)
    timer.daemon = True
    timer.start()
    try:
        changes = refresh(names=[name], cancel=cancel, lookup=lookup, verify=verify)
    except Exception as exc:
        log(f"DNS: свежие адреса {name} узнать не вышло: {exc}", "WARNING")
        changes = []
    finally:
        timer.cancel()
    for item in changes:
        if item.name == name:
            return item.ipv4
    return current_ipv4(name, items[name])


def learn_baseline(*, cancel=None, lookup=None) -> dict[str, list[str]]:
    """При сборке: {название: проверенные адреса имени DoH} для серверов, чьё имя ответило.

    Ничего не пишет в настройки: итог кладёт в файл сборка (``baseline_json``). Сервер, имя которого
    с машины сборки не ответило (закрыт из этой страны), в итог не входит — у него остаются адреса из кода.
    """
    from dns.custom_servers import parse_doh_template
    from dns.dns_providers import is_encrypted_only

    if lookup is None:
        from dns.doh_lookup import find_doh_addresses

        lookup = find_doh_addresses

    def ask(item) -> tuple[str, list[str]]:
        name, data = item
        try:
            found = lookup(
                parse_doh_template(str(data.get("doh") or "")),
                ipv6=False,
                doh_supported=is_encrypted_only(data),
                cancel=cancel,
            )
        except Exception:
            return name, []
        return name, list(_global_ipv4(getattr(found, "ipv4", ()))[:MAX_ADDRESSES])

    items = tracked()
    with ThreadPoolExecutor(max_workers=PARALLEL_SERVERS) as pool:
        answers = list(pool.map(ask, items))
    return {name: addresses for name, addresses in answers if addresses}


def baseline_json(mapping: dict[str, list[str]]) -> str:
    import json

    return json.dumps({name: list(values) for name, values in sorted(mapping.items())}, ensure_ascii=False, indent=2) + "\n"
