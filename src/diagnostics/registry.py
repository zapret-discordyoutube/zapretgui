"""Отметка «в реестре РКН»: значится ли сайт или его адрес в реестре блокировок.

Откуда данные. Открытый список ``bol-van/rulist`` на GitHub: выгрузка реестра
Роскомнадзора, разобранная на имена сайтов (около 1,7 млн) и на адреса,
заблокированные целиком. Список скачивается не чаще раза в сутки и хранится на
компьютере: проверяемые сайты никуда не отправляются, поиск идёт локально.

Как хранится. Полтора миллиона строк в памяти заняли бы сотни мегабайт,
поэтому от каждого имени остаётся 8 байт отпечатка; отпечатки отсортированы и
лежат одним файлом (~14 МБ), поиск — делением пополам. Совпадение двух разных
имён по отпечатку практически невозможно (один шанс на миллиарды).

Что значит отметка. «В реестре» — имя или адрес названы в реестре: сайт
заблокирован официально. «Не значится» — НЕ значит «не блокируют»: YouTube,
X и многое другое режут без записи в реестре. Поэтому отметка — справка
рядом с результатом проверки, а не её вывод.

Здесь нет сети: скачивание передаётся снаружи (``fetch``), чтобы в сеть ходил
только ``diagnostics.net_access``.
"""

from __future__ import annotations

import bisect
import hashlib
import io
import ipaddress
import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "HOSTS_URL",
    "IPS_URL",
    "MAX_AGE_S",
    "STATE_FRESH",
    "STATE_MISSING",
    "STATE_STALE",
    "Fetched",
    "Index",
    "Match",
    "annotate",
    "lines",
    "summary",
    "build_hosts",
    "build_networks",
    "default_folder",
    "load",
    "refresh",
]

_BASE_URL = "https://raw.githubusercontent.com/bol-van/rulist/main/"
HOSTS_URL = _BASE_URL + "reestr_hostname.txt"
IPS_URL = _BASE_URL + "reestr_ipban4.txt"

MAX_AGE_S = 24 * 3600
# Список старше недели всё ещё полезен, но об этом надо сказать.
STALE_AFTER_S = 7 * 24 * 3600

STATE_FRESH = "fresh"
STATE_STALE = "stale"
STATE_MISSING = "missing"

_KEY_BYTES = 8
_HOSTS_FILE = "hosts.idx"
_IPS_FILE = "networks.txt"
_META_FILE = "meta.json"
# Меньше этого — скачалось что-то не то (страница ошибки, обрезанный файл): старый список не трогаем.
_MIN_HOSTS = 100_000


@dataclass(frozen=True, slots=True)
class Fetched:
    """Ответ на скачивание. ``body is None`` при ``unchanged`` — на сервере та же версия."""

    body: bytes | None = None
    etag: str = ""
    unchanged: bool = False


Fetch = Callable[[str, str], "Fetched | None"]


@dataclass(frozen=True, slots=True)
class Match:
    # Имя из реестра, под которое попал сайт: он сам или домен выше. Пусто — не значится.
    name: str = ""
    # Сеть из списка заблокированных адресов, в которую попал адрес сервера. Пусто — не значится.
    network: str = ""

    @property
    def listed(self) -> bool:
        return bool(self.name or self.network)


# Сборка списка идёт фоном во время проверки: через сколько строк и на сколько уступать окну.
_BUILD_STEP = 20_000
_BUILD_PAUSE_S = 0.002


def _clean(host: str) -> str:
    return str(host or "").strip().strip('".').lower()


def _key(host: str) -> bytes:
    return hashlib.blake2b(host.encode("utf-8", errors="ignore"), digest_size=_KEY_BYTES).digest()


def build_hosts(lines: Iterable[str]) -> tuple[bytes, int]:
    """Отсортированные отпечатки имён одним куском и их число."""
    # В списке больше полутора миллионов имён. Одна общая сортировка держала бы
    # интерпретатор полсекунды без передышки — и на это время замирало окно
    # программы. Поэтому отпечатки раскладываются по первому байту на 256 кучек:
    # каждая сортируется за миллисекунды, а вместе они уже идут по порядку.
    # Раз в ``_BUILD_STEP`` строк поток уступает дорогу окну.
    buckets: list[set[bytes]] = [set() for _ in range(256)]
    for number, name in enumerate(map(_clean, lines)):
        if name and "." in name:
            key = _key(name)
            buckets[key[0]].add(key)
        if number % _BUILD_STEP == _BUILD_STEP - 1:
            time.sleep(_BUILD_PAUSE_S)
    parts: list[bytes] = []
    count = 0
    for bucket in buckets:
        parts.append(b"".join(sorted(bucket)))
        count += len(bucket)
        time.sleep(0)
    return b"".join(parts), count


def build_networks(lines: Iterable[str]) -> list[tuple[int, int, str]]:
    """Заблокированные адреса и сети: (первый адрес, последний, запись), по возрастанию."""
    found: list[tuple[int, int, str]] = []
    for line in lines:
        text = str(line or "").strip()
        if not text or text.startswith("#"):
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            continue
        if network.version == 4:
            found.append((int(network.network_address), int(network.broadcast_address), text))
    return sorted(found)


@dataclass(frozen=True, slots=True)
class Index:
    hosts: bytes = b""
    networks: tuple[tuple[int, int, str], ...] = ()
    # Когда список скачан (время компьютера). 0 — списка нет.
    updated: float = 0.0
    # Когда список последний раз сверяли с сервером.
    checked: float = 0.0
    etags: tuple[tuple[str, str], ...] = ()

    @property
    def count(self) -> int:
        return len(self.hosts) // _KEY_BYTES

    def state(self, now: float | None = None) -> str:
        if not self.hosts:
            return STATE_MISSING
        age = (time.time() if now is None else now) - self.updated
        return STATE_STALE if age > STALE_AFTER_S else STATE_FRESH

    def _has(self, name: str) -> bool:
        key = _key(name)
        low, high = 0, self.count
        while low < high:
            middle = (low + high) // 2
            item = self.hosts[middle * _KEY_BYTES : (middle + 1) * _KEY_BYTES]
            if item == key:
                return True
            if item < key:
                low = middle + 1
            else:
                high = middle
        return False

    def find_host(self, host: str) -> str:
        """Имя из реестра, под которое попадает сайт: он сам, без ``www.`` или домен выше."""
        name = _clean(host)
        if not name or not self.hosts:
            return ""
        labels = name.split(".")
        # До домена второго уровня: «com» и «ru» в реестре не ищем.
        for start in range(0, max(1, len(labels) - 1)):
            candidate = ".".join(labels[start:])
            if self._has(candidate):
                return candidate
        return ""

    def find_ip(self, ip: str) -> str:
        """Запись списка заблокированных адресов, в которую попадает ``ip``."""
        if not self.networks or not ip or ":" in ip:
            return ""
        try:
            value = int(ipaddress.IPv4Address(ip))
        except ValueError:
            return ""
        # Сети отсортированы по началу; нужная могла начаться раньше — смотрим несколько назад.
        position = bisect.bisect_right(self.networks, (value, 1 << 33, ""))
        for start, end, text in reversed(self.networks[max(0, position - 8) : position]):
            if start <= value <= end:
                return text
        return ""

    def match(self, host: str, ip: str = "") -> Match:
        return Match(self.find_host(host), self.find_ip(ip))


def default_folder() -> Path:
    from config.runtime_layout import APPLICATION_PATHS

    return Path(APPLICATION_PATHS.user_dir) / "registry"


def load(folder: Path) -> Index:
    """Список с диска. Нет файлов или они испорчены — пустой список, без ошибки."""
    try:
        meta = json.loads((folder / _META_FILE).read_text(encoding="utf-8"))
        hosts = (folder / _HOSTS_FILE).read_bytes()
    except (OSError, ValueError):
        return Index()
    if not isinstance(meta, dict) or len(hosts) % _KEY_BYTES:
        return Index()
    try:
        networks = build_networks((folder / _IPS_FILE).read_text(encoding="utf-8").splitlines())
    except OSError:
        networks = []
    etags = meta.get("etags") if isinstance(meta.get("etags"), dict) else {}
    return Index(
        hosts=hosts,
        networks=tuple(networks),
        updated=float(meta.get("updated") or 0.0),
        checked=float(meta.get("checked") or 0.0),
        etags=tuple(sorted((str(key), str(value)) for key, value in etags.items())),
    )


def _lines(body: bytes | None) -> Iterable[str]:
    """Строки скачанного файла по одной: список на полтора миллиона строк целиком в память не кладём."""
    for raw in io.BytesIO(body or b""):
        yield raw.decode("utf-8", errors="ignore")


def _write(path: Path, data: bytes) -> None:
    """Запись через временный файл: прерванная запись не испортит прежний список."""
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def refresh(folder: Path, fetch: Fetch, *, now: float | None = None, max_age: float = MAX_AGE_S) -> Index:
    """Обновляет список, если с последней сверки прошло больше суток. Возвращает то, что есть.

    ``fetch(адрес, прежняя метка версии)`` скачивает файл или сообщает, что он не
    менялся. Любая неудача (нет сети, GitHub недоступен, скачалось не то)
    оставляет прежний список: устаревшая справка лучше никакой.
    """
    moment = time.time() if now is None else now
    current = load(folder)
    if current.hosts and moment - current.checked < max_age:
        return current
    etags = dict(current.etags)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        changed = False
        hosts = fetch(HOSTS_URL, etags.get(HOSTS_URL, "") if current.hosts else "")
        if hosts is None:
            return current
        if not hosts.unchanged:
            index, count = build_hosts(_lines(hosts.body))
            if count < _MIN_HOSTS:
                return current
            _write(folder / _HOSTS_FILE, index)
            etags[HOSTS_URL] = hosts.etag
            changed = True
        ips = fetch(IPS_URL, etags.get(IPS_URL, "") if current.networks else "")
        if ips is not None and not ips.unchanged and build_networks(_lines(ips.body)):
            _write(folder / _IPS_FILE, ips.body or b"")
            etags[IPS_URL] = ips.etag
            changed = True
        meta = {
            "updated": moment if changed or not current.updated else current.updated,
            "checked": moment,
            "etags": etags,
        }
        _write(folder / _META_FILE, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
    except OSError:
        return current
    return load(folder)


def summary(index: Index, now: float | None = None) -> dict:
    """Состояние списка для отчёта: есть ли он, когда скачан и сколько в нём имён."""
    return {"state": index.state(now), "updated": index.updated, "hosts": index.count, "networks": len(index.networks)}


def lines(services: list[dict], index: Index, now: float | None = None) -> list[str]:
    """Раздел «Реестр РКН» для текстового отчёта."""
    out = ["", "━━━━━━━━ Реестр РКН ━━━━━━━━"]
    if not index.hosts:
        out.append("❔ Список реестра скачать не удалось — отметок «в реестре» в этот раз нет")
        return out
    when = time.strftime("%d.%m.%Y", time.localtime(index.updated))
    stale = " (давно не обновлялся: свежий скачать не удалось)" if index.state(now) == STATE_STALE else ""
    out.append(f"ℹ️ Список от {when}{stale}: {index.count} имён сайтов и {len(index.networks)} заблокированных адресов и сетей")
    found = 0
    for service in services:
        for target in service.get("targets") or ():
            mark = target.get("registry") or {}
            if not mark.get("listed"):
                continue
            found += 1
            parts = []
            if mark.get("name"):
                parts.append("имя в реестре" if mark["name"] == target.get("host") else f"в реестре есть {mark['name']}")
            if mark.get("network"):
                parts.append(f"адрес {target.get('address', '')} в списке заблокированных ({mark['network']})")
            out.append(f"📕 {target.get('host', '')}: {'; '.join(parts)}")
    if not found:
        out.append("ℹ️ Ни один из проверенных сайтов в реестре не значится")
    out.append("   Сайт могут блокировать и без записи в реестре — так режут, например, YouTube.")
    return out


# Метка сайта, который значится в списке.
TAG_REGISTRY = {"key": "registry", "text": "в реестре РКН", "state": "info"}


def annotate(services: list[dict], index: Index) -> None:
    """Дописывает в отчёт по сайтам отметку реестра. Пустой список — ничего не пишет."""
    if not index.hosts:
        return
    for service in services:
        for target in service.get("targets") or ():
            found = index.match(str(target.get("host") or ""), str(target.get("address") or ""))
            target["registry"] = {"listed": found.listed, "name": found.name, "network": found.network}
        # Метка сайта лежит в отчёте готовой, как и остальные его метки.
        tags = service.setdefault("tags", [])
        if any((target.get("registry") or {}).get("listed") for target in service.get("targets") or ()):
            if TAG_REGISTRY not in tags:
                tags.append(dict(TAG_REGISTRY))
