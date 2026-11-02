"""Умный движок hosts: какие адреса сервиса сейчас рабочие и в каком порядке их писать.

Каталог hosts — снимок на день выпуска, а адреса устаревают. Хуже того,
браузер Chrome из нескольких строк одного имени берёт только первую (проверено
на Windows 2026-10-13): стоит первому адресу умереть — сайт «иногда не
работает», хотя запасные адреса записаны рядом. Движок решает это на
компьютере пользователя и делает ровно столько, сколько можно доказать.

**Любой сервис.** Если у имени несколько адресов, первым ставится тот, что
лучше ответил пробе (``hosts.address_probe``): подтверждённо живой — перед
неясным, неясный — перед молчащим. Ничего не выбрасывается: у многих
сервисов в hosts стоят обходные адреса или адреса игровых серверов, которые
порт 443 и не слушают, — «молчит» для них не значит «мёртв». У профилей DNS
проверяются только адреса посредников: остальные строки профиля — обычные
адреса сайтов, как их отдал DNS поставщика, трогать их незачем.

**Сервис с «паспортом»** (в hosts стоит настоящий адрес сайта, и сайт отвечает
по HTTPS: YouTube, GitHub). Здесь молчание порта — прямой признак, поэтому
мёртвый адрес убирается. И здесь программа сама ищет адреса: при каждой
проверке спрашивает шифрованный DNS (IPv4, а где есть IPv6 — и IPv6), пробует
найденное той же пробой и складывает живое в копилку — запас, который
проверяется вместе с каталогом и в следующий раз. Кроме того, перебираются
соседи известных адресов — такие же серверы в ближайших подсетях той же сети
(``hosts.address_scan``): быстрым соединением отсеиваются пустые, самые
быстрые из ответивших проходят полную пробу. Каждый живой адрес меряется
несколько раз настоящим соединением: видны и скорость, и потери. Первый адрес уступает место другому, только если у того
заметно меньше потерь или он заметно быстрее. IPv6 пишется, только если адрес по IPv6 на этом
компьютере действительно отвечает, а не просто «у адаптера есть IPv6».

Итог — набор для ``hosts.catalog_overlay``: в нём только сервисы, у которых
строки получились не такими, как в каталоге. Его видят страница, запись в
hosts и сверка при запуске. Сеть сюда приходит снаружи (``probe``,
``resolve``, ``network_ok``): в тестах она подменяется целиком.

Когда что проверяется (``utils.probe_gate``). Человек переключил сервис
(``Why.USER``) — проверяется всё и ищутся адреса. Плановая проверка
(``Why.SCHEDULE``) трогает только то, чем пользуется браузер, — первый
записанный адрес каждого имени, по одному соединению. Ответили — на этом
всё: ни DNS, ни запаса, ни обхода соседей. Первый адрес не ответил — это
сигнал беды (``Why.SIGNAL``): дальше полная проверка и поиск, но только для
сервисов, у которых беда. Так программа без повода не устраивает пачку
соединений к десяткам адресов при каждом запуске.

**Профиль DNS** (Comss DNS, GeoHide…). Его строки — ответы одного DNS-сервера,
а сервер переводит имена на новые посредники без предупреждения. Поэтому при
каждой проверке программа спрашивает сам сервер профиля (``profile_resolve``,
``hosts.profile_dns``) — шифрованным запросом, из сети пользователя. В счёт идут
только адреса посредников: известные каталогу или отданные сервером сразу
нескольким именам (``RELAY_MIN_NAMES``) — обычные адреса сайтов у CDN меняются
постоянно, и гоняться за ними незачем. Если сервер отдаёт посредник, которого
нет в hosts, это сигнал беды и для плановой проверки: новый посредник
проверяется той же пробой (с именем сервиса), и, если он отвечает, становится
первым, а старые посредники, которых сервер больше не отдаёт, убираются.

Осторожность. Если не ответил ни один адрес и связи с интернетом нет, ничего
не меняется: иначе при обрыве «умерли» бы все адреса разом. Имя никогда не остаётся без адреса: если
убирать пришлось бы всё, остаётся каталог. В фоновой проверке адрес убирается
только после двух неудач подряд в разных проверках; сразу — лишь когда человек
сам переключил сервис и ждёт результата. Порядок не меняется без нужды: пока
первый адрес не хуже остальных, строки остаются как были, и hosts не
переписывается из-за пары миллисекунд разницы.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from hosts.address_probe import ALIVE, DEAD, UNKNOWN, Probe
from hosts.address_scan import neighbours
from hosts.catalog_overlay import clean_overlay
from hosts.catalog_repository import HOSTS_PROFILE_ID, HostsCatalog
from utils.probe_gate import Why, may_search

__all__ = ["PASSPORTS", "EngineReport", "Passport", "check_selection"]

MAX_WORKERS = 24
# Сколько проб сразу идёт на один адрес. У сервиса бывает два десятка имён на одном адресе;
# на стенде 2026-10-13 восемнадцать приветствий разом сервер растянул на четыре секунды, а одно
# не дождалось ответа — и рабочий адрес выглядел сомнительным.
MAX_PER_ADDRESS = 4
# Сколько адресов каждого вида писать имени сервиса с паспортом: первый берёт браузер, второй — запас.
KEEP_PER_FAMILY = 2
# Сколько адресов каждого вида сверх каталога проверять у одного имени (копилка и свежие от DNS).
SEEK_PER_FAMILY = 6
# Сколько соседей известных адресов одного имени обойти быстрым соединением за проверку
# и сколько самых быстрых из ответивших (каждого вида) отдать на полную пробу.
SCAN_LIMIT = 400
SCAN_VERIFY = 4
# Сколько найденных адресов каждого вида хранить про запас у одного имени.
POOL_PER_FAMILY = 4
# Первый адрес уступает место другому живому, только если тот быстрее и на столько миллисекунд,
# и во столько раз: иначе шум замера переставлял бы адреса при каждой проверке.
SWITCH_MIN_MS = 30.0
SWITCH_RATIO = 0.6
# Первый адрес уступает место из-за потерь, когда у него без ответа осталось хотя бы столько
# замеров и у другого адреса их меньше на столько же: одна потеря бывает и на здоровой линии.
LOSS_SWITCH = 2
# Новый адрес из ответа сервера профиля считается посредником, если сервер отдал его
# сразу стольким разным именам: посредник один на весь сервис, адрес сайта у CDN — свой у имени.
RELAY_MIN_NAMES = 3


@dataclass(frozen=True, slots=True)
class Passport:
    """Что известно о сервисе, чей настоящий адрес работает в России.

    ``front`` — нейтральное имя того же владельца для приветствия (пусто — имя
    сервиса не режут, приветствие идёт с ним самим). ``fresh`` — свежие адреса
    можно брать у шифрованного DNS.
    """

    front: str = ""
    fresh: bool = True


# Только сервисы, у которых в hosts стоит настоящий адрес сайта. У Discord, Rutor, x.com,
# Supercell адреса обходные или посредника: честный DNS вернул бы адрес, который в России не открывается.
PASSPORTS: dict[str, Passport] = {
    "hosts.youtube": Passport(front="www.google.com"),
    "hosts.github": Passport(),
    "hosts.githubusercontent_ipv6": Passport(),
}


@dataclass(frozen=True, slots=True)
class EngineReport:
    # Была ли связь с интернетом. Нет — ничего не проверялось и не менялось.
    network: bool = True
    checked: int = 0
    # Набор адресов изменился по сравнению с прежним.
    changed: bool = False
    # Сервис → адреса, убранные как неработающие.
    dropped: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Сервис → свежие адреса, взятые у шифрованного DNS.
    added: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Сервисы, у которых не ответил ни один адрес какого-то имени: оставлены адреса каталога.
    silent: tuple[str, ...] = ()
    checked_at: str = ""


def _is_v6(address: str) -> bool:
    return ":" in address


def _profile_rows(catalog: HostsCatalog, service_name: str, profile_id: str) -> list[tuple[str, str]]:
    if profile_id not in catalog.dns_profiles:
        return []
    index = catalog.dns_profiles.index(profile_id)
    return [
        (host.casefold(), values[index].lower())
        for host, values in catalog.service_entries.get(service_name, [])
        if index < len(values) and values[index]
    ]


def _by_host(rows: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for host, address in rows:
        bucket = grouped.setdefault(host, [])
        if address not in bucket:
            bucket.append(address)
    return grouped


def _name_family(host: str) -> str:
    """Две последние части имени: «raw.githubusercontent.com» и «gist.githubusercontent.com» — одно семейство."""
    return ".".join(host.strip(".").split(".")[-2:])


# Места в очереди на первое место: живой — неясный — молчащий.
_RANK_ALIVE, _RANK_UNSURE, _RANK_SILENT = 0, 1, 2


@dataclass(slots=True)
class _Plan:
    """Один выбранный сервис с профилем: что о нём известно и как его проверять."""

    service_id: str
    service_name: str
    profile_id: str
    hosts: dict[str, list[str]]
    passport: Passport | None

    def key(self, host: str, address: str) -> tuple[str, str, str, str]:
        # Посредник и сервер с нейтральным именем отвечают за все имена сервиса разом:
        # такой адрес проверяется один раз. Иначе ответ зависит от имени, но имена одного
        # семейства (avatars0…avatars8.githubusercontent.com) сервер обслуживает одним
        # сертификатом — проба одна на семейство. Проба на каждое имя била по одному адресу
        # двумя десятками соединений: на стенде 2026-10-13 сервер начал отвечать через раз,
        # и рабочий адрес выглядел мёртвым.
        shared = self.profile_id != HOSTS_PROFILE_ID or bool(self.front_for(address))
        return self.service_id, self.profile_id, "" if shared else _name_family(host), address

    def front_for(self, address: str) -> str:
        """Нейтральное имя для пробы адреса. Адрес IPv6 проверяется только настоящим именем сайта.

        Нейтральное имя доказывает, что адрес жив; открывается ли по нему сам сайт — зависит от
        обхода. Для IPv4 этого хватает: без строки в hosts программа пошла бы на такой же адрес.
        Строка IPv6 меняет больше: Windows ставит IPv6 первым, и все программы уходят на него.
        Поэтому IPv6 идёт в hosts, только когда по нему отвечает сам сайт, под своим именем.
        """
        if self.passport is None or _is_v6(address):
            return ""
        return self.passport.front


def check_selection(
    selection: dict[str, str],
    *,
    catalog: HostsCatalog,
    previous: dict | None,
    probe: Callable[[str, str, str], Probe],
    resolve: Callable[[str, bool], list[str]],
    network_ok: Callable[[], bool],
    ipv6: bool,
    why: Why,
    relays: Collection[str] = (),
    recheck: Collection[str] | None = None,
    sweep: Callable[[list[str]], dict[str, float]] | None = None,
    profile_resolve: Callable[[str, str, bool], list[str] | None] | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> tuple[dict | None, EngineReport]:
    """Проверяет адреса выбранных сервисов. Возвращает (новый набор или None, отчёт).

    ``selection`` — «название сервиса → профиль», как его пишет страница.
    ``catalog`` — поставленный каталог без наложений. ``probe(адрес, имя,
    нейтральное имя)`` и ``resolve(имя, нужен_ли_IPv6)`` — сеть. ``sweep(адреса)`` —
    быстрый обход: какие адреса принимают соединение и за сколько (None — соседей
    известных адресов не искать). ``relays`` — адреса посредников каталога. ``why`` — повод
    (``utils.probe_gate.Why``): ``USER`` — человек ждёт результата, мёртвый адрес убирается
    сразу, без второй проверки; ``SCHEDULE`` — сначала только первые адреса, полная проверка
    лишь при беде. ``profile_resolve(профиль, имя, нужен_ли_IPv6)`` — что сейчас
    отдаёт DNS-сервер самого профиля (None — сервера нет или он не ответил).
    ``recheck`` — названия сервисов,
    которые проверить сейчас (None — все): человек переключил один сервис и не
    должен ждать, пока перепроверятся остальные двадцать; у остальных остаются
    адреса прошлой проверки. None вместо набора — менять нечего (нет связи или
    нечего проверять).
    """
    before = clean_overlay(previous)
    same_catalog = before["catalog"] == catalog.content_sha256
    relay_set = {str(address).strip().lower() for address in relays}
    plans: list[_Plan] = []
    carried: dict[tuple[str, str], list[list[str]]] = {}
    selected = 0
    for service_name, profile_id in (selection or {}).items():
        service_id = catalog.service_id_by_name.get(service_name, "")
        rows = _profile_rows(catalog, service_name, profile_id) if service_id and profile_id else []
        if not rows:
            continue
        selected += 1
        if recheck is not None and service_name not in recheck:
            kept = before["services"].get(service_id, {}).get(profile_id) if same_catalog else None
            if kept:
                carried[(service_id, profile_id)] = [list(row) for row in kept]
            continue
        passport = PASSPORTS.get(service_id) if profile_id == HOSTS_PROFILE_ID else None
        plans.append(_Plan(service_id, service_name, profile_id, _by_host(rows), passport))
    if not selected:
        return None, EngineReport()
    strict = why == Why.USER
    fresh_relays = _ask_profile_servers(plans, profile_resolve, relay_set, ipv6)

    def _checked(plan: _Plan, addresses: list[str], address: str) -> bool:
        """Проверять ли адрес. Без паспорта — только там, где есть из чего выбирать первого."""
        if _is_v6(address) and not ipv6:
            return False
        if plan.passport is None:
            rivals = [other for other in addresses if _is_v6(other) == _is_v6(address)]
            if len(rivals) < 2 or (plan.profile_id != HOSTS_PROFILE_ID and address not in relay_set):
                return False
        return True

    if why == Why.SCHEDULE:
        # Плановая проверка: только первый записанный адрес каждого имени — тот, что берёт браузер.
        written = {
            (service_id, profile_id): rows
            for service_id, profiles in before["services"].items()
            for profile_id, rows in profiles.items()
        } if same_catalog else {}
        firsts: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
        owner: dict[tuple[str, str, str, str], str] = {}
        for plan in plans:
            rows = written.get((plan.service_id, plan.profile_id))
            current = _by_host([(str(host), str(address)) for host, address in rows]) if rows else plan.hosts
            for host, addresses in current.items():
                first = next((address for address in addresses if _checked(plan, plan.hosts.get(host, addresses), address)), "")
                if first:
                    key = plan.key(host, first)
                    firsts.setdefault(key, (first, host, plan.front_for(first)))
                    owner.setdefault(key, plan.service_id)
        light = _probe_all(firsts, probe)
        troubled = {owner[key] for key, found in light.items() if found.verdict == DEAD}
        # Сервер профиля отдаёт посредник, которого в hosts нет: тоже беда, даже если старый ещё отвечает.
        for plan in plans:
            rows = written.get((plan.service_id, plan.profile_id))
            current = _by_host([(str(host), str(address)) for host, address in rows]) if rows else plan.hosts
            if any(
                address not in current.get(host, [])
                for host in plan.hosts
                for address in fresh_relays.get((plan.profile_id, host), ())
            ):
                troubled.add(plan.service_id)
        if not troubled:
            # Браузер получает живой адрес: менять нечего, искать незачем.
            return None, EngineReport(checked=len(light), checked_at=before["checked_at"])
        if not any(found.verdict in (ALIVE, UNKNOWN) for found in light.values()) and not network_ok():
            return None, EngineReport(network=False, checked=len(light))
        # Сигнал беды: полная проверка — только сервисам, у которых первый адрес не ответил.
        for plan in plans:
            if plan.service_id not in troubled:
                kept = before["services"].get(plan.service_id, {}).get(plan.profile_id) if same_catalog else None
                if kept:
                    carried[(plan.service_id, plan.profile_id)] = [list(row) for row in kept]
        plans = [plan for plan in plans if plan.service_id in troubled]
        recheck = {plan.service_name for plan in plans}
        why = Why.SIGNAL

    units: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    for plan in plans:
        for host, addresses in plan.hosts.items():
            for address in addresses:
                if _checked(plan, addresses, address):
                    units.setdefault(plan.key(host, address), (address, host, plan.front_for(address)))
    verdicts = _probe_all(units, probe)
    # Есть ли интернет, спрашивается, только когда не ответил вообще никто: отдельная проверка
    # связи стоит секунды, а хоть один ответ (даже «соединился, но молчит») и так всё доказывает.
    if units and not any(found.verdict in (ALIVE, UNKNOWN) for found in verdicts.values()) and not network_ok():
        return None, EngineReport(network=False)

    # Поиск адресов — сервисам с паспортом и только по поводу: человек ждёт или первый адрес
    # не ответил (``may_search``). Сверх каталога проверяются копилка прошлых находок
    # и свежие ответы шифрованного DNS: IPv4 всегда, IPv6 — если он на этом компьютере есть.
    pool_before = before["pool"]
    seekers = [
        (index, plan)
        for index, plan in enumerate(plans)
        if may_search(why) and plan.passport is not None and plan.passport.fresh
    ]
    asked = list(dict.fromkeys(host for _index, plan in seekers for host in plan.hosts))

    def _answers(host: str) -> list[str]:
        try:
            return [str(item).strip().lower() for item in resolve(host, ipv6)]
        except Exception:
            return []

    answered: dict[str, list[str]] = {}
    if asked:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(asked)), thread_name_prefix="hosts-engine-dns") as pool:
            answered = dict(zip(asked, pool.map(_answers, asked)))
    found: dict[tuple[int, str], list[str]] = {}
    found_units: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    nearby: dict[tuple[int, str], list[str]] = {}
    for index, plan in seekers:
        saved = pool_before.get(plan.service_id, {})
        for host, addresses in plan.hosts.items():
            taken = {False: 0, True: 0}
            extra = found.setdefault((index, host), [])
            for address in [*saved.get(host, []), *answered.get(host, [])]:
                family = _is_v6(address)
                if (family and not ipv6) or address in addresses or address in extra or taken[family] >= SEEK_PER_FAMILY:
                    continue
                taken[family] += 1
                extra.append(address)
            if sweep is not None:
                # Соседи: те же «номера серверов» в ближайших подсетях той же сети (hosts.address_scan).
                known = [address for address in [*addresses, *extra] if ipv6 or not _is_v6(address)]
                nearby[(index, host)] = neighbours(known, limit=SCAN_LIMIT)
    if nearby:
        try:
            accepted = sweep(list(dict.fromkeys(address for group in nearby.values() for address in group)))
        except Exception:
            accepted = {}
        for (index, host), group in nearby.items():
            for family in (False, True):
                fastest = sorted(
                    (address for address in group if address in accepted and _is_v6(address) == family),
                    key=accepted.__getitem__,
                )
                found[(index, host)].extend(fastest[:SCAN_VERIFY])
    if may_search(why):
        # Профиль DNS: кандидаты — посредники, которых сервер профиля отдаёт сейчас.
        for index, plan in enumerate(plans):
            if plan.passport is not None:
                continue
            for host, addresses in plan.hosts.items():
                fresh = [address for address in fresh_relays.get((plan.profile_id, host), ()) if address not in addresses]
                if fresh:
                    found.setdefault((index, host), []).extend(fresh[: SEEK_PER_FAMILY * 2])
    for (index, host), extra in found.items():
        plan = plans[index]
        for address in extra:
            key = plan.key(host, address)
            if key not in verdicts:
                found_units.setdefault(key, (address, host, plan.front_for(address)))
    verdicts.update(_probe_all(found_units, probe))

    remembered = {
        (service_id, profile_id): [(str(host), str(address)) for host, address in rows]
        for service_id, profiles in before["services"].items()
        for profile_id, rows in profiles.items()
    } if same_catalog else {}
    suspects_before = set(before["suspects"]) if same_catalog else set()
    # Подозрение с адресов, которые сейчас не проверялись, не снимается.
    suspects_after: set[str] = {address for rows in carried.values() for _host, address in rows if address in suspects_before}
    services: dict[str, dict[str, list[list[str]]]] = {}
    for (service_id, profile_id), rows in carried.items():
        services.setdefault(service_id, {})[profile_id] = rows
    pool_after: dict[str, dict[str, list[str]]] = {sid: {host: list(items) for host, items in hosts.items()} for sid, hosts in pool_before.items()}
    dropped: dict[str, set[str]] = {}
    added: dict[str, set[str]] = {}
    silent: list[str] = []
    for index, plan in enumerate(plans):
        full = plan.passport is not None
        result: list[list[str]] = []
        answered_any = False
        for host, addresses in plan.hosts.items():
            ranked: list[tuple[str, int]] = []
            score: dict[str, tuple[int, float]] = {}
            doomed: list[str] = []
            unwritten: list[str] = []
            doubted: list[str] = []
            for address in addresses:
                probed = verdicts.get(plan.key(host, address))
                verdict = probed.verdict if probed is not None else None
                if verdict == ALIVE:
                    ranked.append((address, _RANK_ALIVE))
                    score[address] = _score(probed)
                    answered_any = True
                elif verdict == DEAD:
                    ranked.append((address, _RANK_SILENT))
                    if full and (strict or address in suspects_before):
                        doomed.append(address)
                    elif full:
                        # Первая неудача в фоновой проверке: запомнить и перепроверить в следующий раз.
                        doubted.append(address)
                else:
                    # Проба ничего не доказала — или адрес не проверялся вовсе (IPv6 без IPv6, не посредник).
                    ranked.append((address, _RANK_UNSURE))
                    answered_any = answered_any or verdict == UNKNOWN
            # Найденный адрес идёт в hosts, только если сам прошёл пробу.
            extra = found.get((index, host), [])
            proven: list[str] = []
            for address in extra:
                probed = verdicts.get(plan.key(host, address))
                if probed is not None and probed.verdict == ALIVE:
                    proven.append(address)
                    score[address] = _score(probed)
            ranked.extend((address, _RANK_ALIVE) for address in sorted(proven, key=score.__getitem__))
            handed = set(fresh_relays.get((plan.profile_id, host), ())) if plan.passport is None else set()
            if handed and any(address in handed and rank == _RANK_ALIVE for address, rank in ranked):
                # Сервер профиля перевёл имя на другой посредник, и тот отвечает: старые посредники,
                # которых сервер больше не отдаёт, из hosts уходят. Адреса сайтов не трогаются.
                doomed.extend(
                    address
                    for address in addresses
                    if address in relay_set and address not in handed and address not in doomed
                )
            if full:
                # Адрес IPv4 считается мёртвым, только когда рядом отвечает другой IPv4. Не ответил ни
                # один — значит, слепа проба (ей мешает обход, антивирус, сбой связи), а не умерли все
                # адреса сразу. Иначе имя оставалось с одними IPv6: Windows тогда отдаёт программам
                # только их и про IPv4 у DNS уже не спрашивает (отчёт владельца 2026-10-14).
                if not any(rank == _RANK_ALIVE and not _is_v6(address) for address, rank in ranked):
                    blind = [address for address, _rank in ranked if not _is_v6(address)]
                    doomed = [address for address in doomed if address not in blind]
                    doubted = [address for address in doubted if address not in blind]
                if ipv6 and any(not _is_v6(address) for address, _rank in ranked):
                    # IPv6 — добавка к IPv4, а не замена: в hosts идёт только адрес, по которому сайт
                    # сейчас отвечает без потерь. Остальные просто не пишутся — и проверяются снова
                    # в следующий раз.
                    unwritten = [
                        address
                        for address, rank in ranked
                        if _is_v6(address)
                        and address not in doomed
                        and not (rank == _RANK_ALIVE and score[address][0] < LOSS_SWITCH)
                    ]
            suspects_after.update(doubted)
            # Имя не остаётся без адреса, которым этот компьютер может пользоваться.
            usable = [address for address, _rank in ranked if address not in doomed and (ipv6 or not _is_v6(address))]
            if not usable:
                doomed = []
            ranked = [(address, rank) for address, rank in ranked if address not in unwritten]
            before_rows = [address for row_host, address in remembered.get((plan.service_id, plan.profile_id), ()) if row_host == host]
            ordered = _order(
                [(address, rank) for address, rank in ranked if address not in doomed],
                remembered=before_rows,
                first_v6=_is_v6(addresses[0]),
                keep=KEEP_PER_FAMILY if full else 0,
                score=score if full else None,
            )
            if doomed:
                dropped.setdefault(plan.service_name, set()).update(doomed)
            newly = [address for address in ordered if address not in addresses and address not in before_rows]
            if newly:
                added.setdefault(plan.service_name, set()).update(newly)
            result.extend([host, address] for address in ordered)
            if full and plan.passport.fresh:
                # Копилка: живые находки от быстрой к медленной; заминка не выбрасывает адрес,
                # явный отказ — выбрасывает. Адреса другого вида (IPv6 без IPv6) ждут своего часа.
                known = pool_before.get(plan.service_id, {}).get(host, [])
                alive_found = sorted(
                    (address for address in extra if address in score), key=lambda address: score[address]
                )
                waiting = [
                    address
                    for address in known
                    if address not in score
                    and ((probed := verdicts.get(plan.key(host, address))) is None or probed.verdict != DEAD)
                ]
                kept_pool: list[str] = []
                for address in [*alive_found, *waiting]:
                    same_kind = [item for item in kept_pool if _is_v6(item) == _is_v6(address)]
                    if address not in kept_pool and len(same_kind) < POOL_PER_FAMILY:
                        kept_pool.append(address)
                bucket = pool_after.setdefault(plan.service_id, {})
                if kept_pool:
                    bucket[host] = kept_pool
                else:
                    bucket.pop(host, None)
        if full and not answered_any and any(key[:2] == (plan.service_id, plan.profile_id) for key in verdicts):
            silent.append(plan.service_name)
        # В наборе — только то, что отличается от каталога: настройки не пухнут сотнями одинаковых строк.
        if result != [[host, address] for host, addresses in plan.hosts.items() for address in addresses]:
            services.setdefault(plan.service_id, {})[plan.profile_id] = result

    # Время проверки — когда проверялись все: частичная проверка одного сервиса его не обновляет.
    partial = recheck is not None and same_catalog and bool(before["checked_at"])
    checked_at = before["checked_at"] if partial else now().isoformat(timespec="seconds")
    after = clean_overlay(
        {
            "catalog": catalog.content_sha256,
            "checked_at": checked_at,
            "suspects": sorted(suspects_after),
            "services": services,
            "pool": pool_after,
        }
    )
    report = EngineReport(
        network=True,
        checked=len(verdicts),
        changed=after["services"] != (before["services"] if same_catalog else {}),
        dropped={name: tuple(sorted(items)) for name, items in dropped.items()},
        added={name: tuple(sorted(items)) for name, items in added.items()},
        silent=tuple(silent),
        checked_at=checked_at,
    )
    return after, report


def _ask_profile_servers(
    plans: list[_Plan],
    profile_resolve: Callable[[str, str, bool], list[str] | None] | None,
    relay_set: set[str],
    ipv6: bool,
) -> dict[tuple[str, str], list[str]]:
    """(профиль, имя) → посредники, которые сервер профиля отдаёт сейчас. Пусто — спросить нечего.

    Посредником считается адрес, известный каталогу как посредник, или отданный
    сервером сразу ``RELAY_MIN_NAMES`` разным именам этого профиля.
    """
    if profile_resolve is None:
        return {}
    asked = list(
        dict.fromkeys(
            (plan.profile_id, host)
            for plan in plans
            if plan.passport is None and plan.profile_id != HOSTS_PROFILE_ID
            for host in plan.hosts
        )
    )
    if not asked:
        return {}

    def _answer(item: tuple[str, str]) -> list[str] | None:
        try:
            answer = profile_resolve(item[0], item[1], ipv6)
        except Exception:
            return None
        return None if answer is None else [str(address).strip().lower() for address in answer]

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(asked)), thread_name_prefix="hosts-engine-profile-dns") as pool:
        answers = dict(zip(asked, pool.map(_answer, asked)))
    names_by_address: dict[tuple[str, str], set[str]] = {}
    for (profile_id, host), answer in answers.items():
        for address in answer or ():
            names_by_address.setdefault((profile_id, address), set()).add(host)
    result: dict[tuple[str, str], list[str]] = {}
    for (profile_id, host), answer in answers.items():
        relays = [
            address
            for address in answer or ()
            if (ipv6 or not _is_v6(address))
            and (address in relay_set or len(names_by_address[(profile_id, address)]) >= RELAY_MIN_NAMES)
        ]
        if relays:
            result[(profile_id, host)] = relays
    return result


def _probe_all(
    units: dict[tuple[str, str, str, str], tuple[str, str, str]],
    probe: Callable[[str, str, str], Probe],
) -> dict[tuple[str, str, str, str], Probe]:
    if not units:
        return {}
    gates = {unit[0]: threading.BoundedSemaphore(MAX_PER_ADDRESS) for unit in units.values()}

    def _gated(address: str, host: str, front: str) -> Probe:
        with gates[address]:
            return probe(address, host, front)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(units)), thread_name_prefix="hosts-engine") as pool:
        futures = {key: pool.submit(_gated, *unit) for key, unit in units.items()}
        verdicts: dict[tuple[str, str, str, str], Probe] = {}
        for key, future in futures.items():
            try:
                verdicts[key] = future.result()
            except Exception:
                verdicts[key] = Probe(UNKNOWN, reason="проба не выполнилась")
    return verdicts


def _verdict_of(verdicts: dict, plan: _Plan, host: str, address: str) -> str | None:
    found = verdicts.get(plan.key(host, address))
    return found.verdict if found is not None else None


def _score(probed: Probe) -> tuple[int, float]:
    """Чем сравнивать живые адреса: сначала потери, потом время соединения."""
    if probed.connect_ms is not None:
        return int(probed.lost), float(probed.connect_ms)
    return int(probed.lost), float(probed.ms) if probed.ms is not None else 0.0


_NO_SCORE = (10**6, float("inf"))


def _order(
    ranked: list[tuple[str, int]],
    *,
    remembered: list[str],
    first_v6: bool,
    keep: int,
    score: dict[str, tuple[int, float]] | None = None,
) -> list[str]:
    """Порядок адресов одного имени. ``ranked`` — (адрес, место в очереди) в порядке каталога.

    Прежний порядок сохраняется, пока он годится: иначе блок в hosts переписывался бы при
    каждой проверке. Меняется он только по делу — первым в каждом виде (IPv4, IPv6) должен
    стоять адрес с лучшим ответом из имеющихся: именно первый берёт браузер. ``keep`` —
    сколько адресов каждого вида оставить (0 — все); лишними становятся худшие.

    ``score`` — (потери, время соединения) живых адресов, только у сервисов с паспортом.
    С ним первый адрес уступает место другому живому в двух случаях: у первого теряются
    пакеты, а у другого заметно меньше; или потерь не больше, а соединение заметно быстрее.
    Одна потеря или пара миллисекунд от замера к замеру — шум, а не повод переписывать hosts.
    """

    def family(want_v6: bool) -> list[str]:
        rank = {address: place for address, place in ranked if _is_v6(address) == want_v6}
        if not rank:
            return []
        kept = [address for address in dict.fromkeys(remembered) if address in rank]
        # Отправная точка — порядок каталога, найденные адреса за ним: адрес каталога проверен
        # из многих сетей и уступает первое место только заметно лучшему.
        ordered = kept + [address for address in rank if address not in kept]
        best = min(rank.values())
        leaders = [address for address in ordered if rank[address] == best]
        top = min(leaders, key=lambda address: score.get(address, _NO_SCORE)) if score is not None else leaders[0]
        head = ordered[0]
        if rank[head] != best:
            head = top
        elif score is not None and top != head and head in score and top in score:
            head_lost, head_ms = score[head]
            top_lost, top_ms = score[top]
            steadier = head_lost >= LOSS_SWITCH and top_lost + LOSS_SWITCH <= head_lost
            faster = top_lost <= head_lost and top_ms + SWITCH_MIN_MS <= head_ms and top_ms <= head_ms * SWITCH_RATIO
            if steadier or faster:
                head = top
        ordered.remove(head)
        ordered.insert(0, head)
        if keep and len(ordered) > keep:
            # Запасным остаётся адрес без заметных потерь, если такой есть.
            lossy = (lambda address: score.get(address, (0, 0.0))[0] >= LOSS_SWITCH) if score is not None else (lambda address: False)
            ordered = [ordered[0]] + sorted(ordered[1:], key=lambda address: (rank[address], lossy(address)))[: keep - 1]
        return ordered

    v4, v6 = family(False), family(True)
    return v6 + v4 if first_v6 else v4 + v6
