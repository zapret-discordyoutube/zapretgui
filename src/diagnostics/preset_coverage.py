"""Какой профиль запущенного пресета ловит имя сайта — по тем же правилам, что winws2.

Когда Zapret запущен, а сайт всё равно закрыт, причин две, и советы у них разные:

- имя попадает в профиль, но его стратегия этот фильтр не обходит — нужен подбор стратегии;
- имя не попадает ни в один профиль — Zapret его не трогает, и подбирать нечего: имя надо
  добавить в профиль (или выбрать пресет, где оно есть).

Источник истины — то, что работает: winws2 запущен как ``winws2.exe @файл``, и в этом файле
ровно аргументы запущенного пресета. Правила выбора профиля взяты из руководства zapret2
(«Profiles», «Filtering by lists»):

- профили проверяются по порядку, выигрывает первый подошедший;
- группы портов tcp/udp/icmp/ipp: нет ни одной — подходит всё; есть хоть одна — неуказанные закрыты;
- ``--filter-l7`` — подходит, только если в нём есть протокол соединения (tls для TCP 443, quic для UDP 443);
- ipset — адрес должен быть в списке включения и не быть в списке исключения;
- hostlist — имя должно быть в списке включения (поддомены сами, ``^`` — без поддоменов,
  ``!`` — местное исключение) и не быть в списке исключения; с автосписком профиль
  выбирается при любом имени.

Шаблоны (``--template``) не рабочие профили и пропускаются; ``--import`` не разворачивается.

Чтобы отчёт не заставлял гадать, строка называет и пресет, и стратегию профиля. Пресет узнаётся
по имени файла ``@…``: zapret2_runner и zapret1_runner называют его
``winws2_at_<sha1(путь пресета, NUL, текст)>`` (``winws1_at_…``), так что достаточно найти файл
пресета, который даёт тот же отпечаток. Стратегия — по каталогам готовых стратегий рядом с
программой, тем же поиском, что и на странице профилей; своя стратегия показывается кратко как есть.

Оркестратор запускает winws2 как ``winws2.exe @<папка>/user/lua/circular-config.runtime.txt``:
его профили перебирают пронумерованные стратегии, и для каждого сайта он сам закрепляет рабочую
(настройки ``orchestra.locked.<tls|quic|…>``: имя → номер). Тогда строка говорит не про пресет,
а какую стратегию оркестратор закрепил за этим именем — или что ещё перебирает.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import ipaddress
import os
import re
import shlex
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath

__all__ = ["Coverage", "RunningPreset", "annotate", "parse_launch_args", "read_at_config", "running_preset"]

PROTOCOL_TLS = "tls"
PROTOCOL_QUIC = "quic"

_HOSTLIST_FILES = ("--hostlist",)
_HOSTLIST_EXCLUDE_FILES = ("--hostlist-exclude",)
_HOSTLIST_INLINE = ("--hostlist-domains",)
_HOSTLIST_EXCLUDE_INLINE = ("--hostlist-exclude-domains",)
_IPSET_FILES = ("--ipset",)
_IPSET_EXCLUDE_FILES = ("--ipset-exclude",)
_IPSET_INLINE = ("--ipset-ip",)
_IPSET_EXCLUDE_INLINE = ("--ipset-exclude-ip",)
_STRATEGY_PREFIXES = ("--lua-desync", "--dpi-desync")
# Функции zapret2, которые ничего не меняют: ``pass`` только отмечает вызов (профили-исключения
# пропускают им трафик без обхода), ``pktdebug`` пишет пакет в лог.
_NO_OP_DESYNC = frozenset({"pass", "pktdebug", "none"})
# Оркестраторы zapret2: сами не обходят, а перебирают стратегии профиля с пометкой ``:strategy=N``.
_ORCHESTRATOR_DESYNC = frozenset({"circular", "circular_quality"})
_STRATEGY_NUMBER = re.compile(r":strategy=(\d+)(?=:|$)")
# Папки в полных путях, которые программа пишет в ``@…`` (у winws): в отчёте хватит имени файла.
_WINDOWS_DIRS = re.compile(r"[A-Za-z]:\\(?:[^\\\s:,]+\\)*")
_POSIX_DIRS = re.compile(r"(?<=[=:,])/(?:[^/\s:,]+/)+")
# Файл, с которым оркестратор запускает winws2 (orchestra_runner, ``runtime_config_path``).
ORCHESTRA_AT_CONFIG_NAME = "circular-config.runtime.txt"
# Своя стратегия показывается в строке кратко: длиннее — обрезается.
_STRATEGY_TEXT_LIMIT = 160
ENGINE_WINWS1 = "winws1"
ENGINE_WINWS2 = "winws2"


@dataclass(frozen=True, slots=True)
class Coverage:
    """Чем кончился поиск профиля для имени.

    ``profile`` — имя профиля; пусто — ни один профиль имя не ловит. ``via`` — что совпало
    («список youtube.txt», «адрес в ipset-youtube.txt», «все сайты»). ``strategy`` — есть ли
    в профиле стратегия (профили-исключения нарочно пропускают трафик без неё).
    """

    profile: str = ""
    via: str = ""
    strategy: bool = True
    # Название готовой стратегии профиля; пусто — не узнали (или стратегии нет).
    strategy_name: str = ""
    # Стратегия профиля не совпала ни с одной готовой («своя стратегия»).
    custom_strategy: bool = False
    # Название запущенного пресета; пусто — не узнали.
    preset: str = ""
    # Стратегия профиля кратко, как написана (для своей стратегии и закреплённой оркестратором).
    strategy_text: str = ""
    # Имя ловит профиль оркестратора: стратегию для сайта выбирает он сам.
    orchestra: bool = False
    # Какую стратегию профиля оркестратор закрепил за этим именем; 0 — пока не закрепил.
    locked_strategy: int = 0

    @property
    def covered(self) -> bool:
        return bool(self.profile)


@dataclass(slots=True)
class _Hosts:
    entries: list[tuple[str, bool, bool]] = field(default_factory=list)  # (имя, без поддоменов, местное исключение)

    def add(self, line: str) -> None:
        text = line.split("#", 1)[0].strip().lower().rstrip(".")
        if not text:
            return
        exact = local_exclusion = False
        while text[:1] in ("^", "!"):
            exact = exact or text[0] == "^"
            local_exclusion = local_exclusion or text[0] == "!"
            text = text[1:]
        if text:
            self.entries.append((text, exact, local_exclusion))

    def matches(self, host: str) -> bool:
        hit = False
        for name, exact, local_exclusion in self.entries:
            if host == name or (not exact and host.endswith("." + name)):
                if local_exclusion:
                    return False
                hit = True
        return hit


@dataclass(slots=True)
class _Profile:
    index: int
    name: str = ""
    template: bool = False
    strategy: bool = False
    tcp: list[str] = field(default_factory=list)
    udp: list[str] = field(default_factory=list)
    other_groups: bool = False
    l7: list[str] = field(default_factory=list)
    hosts: _Hosts = field(default_factory=_Hosts)
    hosts_exclude: _Hosts = field(default_factory=_Hosts)
    host_files: list[str] = field(default_factory=list)
    autohostlist: bool = False
    named_strategy: str = ""
    custom_strategy: bool = False
    # Строки стратегии как есть (без ``pass``/``pktdebug``) — для своей стратегии в отчёте.
    strategy_lines: list[str] = field(default_factory=list)
    # Профиль оркестратора: ключ его записей (tls, quic, …) и стратегии по номерам.
    orchestra_key: str = ""
    numbered: dict[int, list[str]] = field(default_factory=dict)
    ips: list = field(default_factory=list)
    ips_exclude: list = field(default_factory=list)
    ip_files: list[str] = field(default_factory=list)

    def title(self) -> str:
        return self.name or f"профиль {self.index}"


def _option(arg: str) -> tuple[str, str]:
    if "=" in arg:
        name, value = arg.split("=", 1)
        return name.strip().lower(), value.strip()
    return arg.strip().lower(), ""


def _ports_match(specs: Iterable[str], port: int) -> bool:
    for spec in specs:
        for token in spec.split(","):
            token = token.strip()
            negate = token.startswith("~")
            token = token.lstrip("~")
            if not token:
                continue
            if token == "*":
                inside = True
            elif "-" in token:
                low, _sep, high = token.partition("-")
                try:
                    inside = int(low or 0) <= port <= int(high or 65535)
                except ValueError:
                    continue
            else:
                try:
                    inside = int(token) == port
                except ValueError:
                    continue
            if inside != negate:
                return True
    return False


def _networks(lines: Iterable[str]) -> list:
    found = []
    for line in lines:
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        try:
            found.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return found


def _read_lines(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return []
    return raw.decode("utf-8", errors="replace").splitlines()


def _desync_param(value: str, key: str) -> str:
    """Значение параметра функции из ``--lua-desync=функция:ключ=значение:…``; пусто — нет такого."""
    for part in value.split(":")[1:]:
        name, sep, param = part.partition("=")
        if sep and name.strip().lower() == key:
            return param.strip()
    return ""


def parse_launch_args(
    args: Sequence[str],
    base_dir: Path,
    *,
    engine: str = ENGINE_WINWS2,
    read_lines: Callable[[Path], list[str]] = _read_lines,
) -> "RunningPreset":
    """Профили из аргументов winws2 (winws). Пути списков — от рабочей папки (``base_dir``).

    У winws (Zapret 1) нет ``--name``: название профиля программа пишет в ``--comment``.
    """
    profiles: list[_Profile] = [_Profile(index=1)]
    named: set[int] = set()

    def resolve(value: str) -> Path:
        path = Path(value.strip().strip('"'))
        return path if path.is_absolute() else base_dir / path

    for arg in args:
        name, value = _option(arg)
        current = profiles[-1]
        if name == "--new":
            profiles.append(_Profile(index=len(profiles) + 1))
        elif name == "--name":
            current.name = value
            named.add(current.index)
        elif name == "--comment" and engine == ENGINE_WINWS1 and current.index not in named and not current.name:
            current.name = value
        elif name == "--template":
            current.template = True
        elif name == "--filter-tcp":
            current.tcp.append(value)
        elif name == "--filter-udp":
            current.udp.append(value)
        elif name in ("--filter-icmp", "--filter-ipp"):
            current.other_groups = True
        elif name == "--filter-l7":
            current.l7.extend(item.strip().lower() for item in value.split(",") if item.strip())
        elif name in _HOSTLIST_FILES:
            current.host_files.append(PurePath(value).name)
            for line in read_lines(resolve(value)):
                current.hosts.add(line)
        elif name in _HOSTLIST_EXCLUDE_FILES:
            for line in read_lines(resolve(value)):
                current.hosts_exclude.add(line)
        elif name in _HOSTLIST_INLINE:
            current.host_files.append("--hostlist-domains")
            for item in value.split(","):
                current.hosts.add(item)
        elif name in _HOSTLIST_EXCLUDE_INLINE:
            for item in value.split(","):
                current.hosts_exclude.add(item)
        elif name == "--hostlist-auto":
            current.autohostlist = True
            current.host_files.append(PurePath(value).name)
            for line in read_lines(resolve(value)):
                current.hosts.add(line)
        elif name in _IPSET_FILES:
            current.ip_files.append(PurePath(value).name)
            current.ips.extend(_networks(read_lines(resolve(value))))
        elif name in _IPSET_EXCLUDE_FILES:
            current.ips_exclude.extend(_networks(read_lines(resolve(value))))
        elif name in _IPSET_INLINE:
            current.ip_files.append("--ipset-ip")
            current.ips.extend(_networks(value.split(",")))
        elif name in _IPSET_EXCLUDE_INLINE:
            current.ips_exclude.extend(_networks(value.split(",")))
        elif name.startswith(_STRATEGY_PREFIXES):
            function = value.split(":", 1)[0].strip().lower()
            if function in _ORCHESTRATOR_DESYNC:
                current.strategy = True
                current.orchestra_key = (_desync_param(value, "key") or function).lower()
            elif function and function not in _NO_OP_DESYNC:
                current.strategy = True
                current.strategy_lines.append(arg.strip())
                number = _STRATEGY_NUMBER.search(value)
                if number is not None:
                    current.numbered.setdefault(int(number.group(1)), []).append(arg.strip())
    return RunningPreset(tuple(profile for profile in profiles if not profile.template))


def strategy_text(lines: Iterable[str]) -> str:
    """Стратегия кратко, как написана: строки через пробел, пути — именами файлов, без номеров оркестратора."""
    parts = []
    for line in lines:
        text = _STRATEGY_NUMBER.sub("", str(line or "").strip())
        text = _POSIX_DIRS.sub("", _WINDOWS_DIRS.sub("", text))
        if text:
            parts.append(text)
    joined = " ".join(parts)
    return joined if len(joined) <= _STRATEGY_TEXT_LIMIT else joined[: _STRATEGY_TEXT_LIMIT - 1].rstrip() + "…"


def locked_number(locked: Mapping[str, object], host: str) -> int:
    """Номер стратегии, закреплённой оркестратором за именем; 0 — не закреплена.

    Оркестратор хранит запись под именем сайта или под его последними уровнями (``nld``):
    ищем само имя, затем его родителей — от длинного к короткому, но не короче двух уровней.
    """
    labels = str(host or "").strip().lower().rstrip(".").split(".")
    for start in range(0, max(1, len(labels) - 1)):
        key = ".".join(labels[start:])
        value = locked.get(key) if key else None
        try:
            number = int(value) if value is not None else 0
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            return number
    return 0


@dataclass(frozen=True, slots=True)
class RunningPreset:
    profiles: tuple[_Profile, ...]
    preset: str = ""
    # Закреплённые оркестратором стратегии: ключ профиля (tls, quic, …) → имя → номер.
    locked: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def covering(self, host: str, ip: str = "", *, protocol: str = PROTOCOL_TLS, port: int = 443) -> Coverage:
        """Первый профиль, который winws2 выберет для соединения с этим именем и адресом."""
        host = str(host or "").strip().lower().rstrip(".")
        try:
            address = ipaddress.ip_address(ip) if ip else None
        except ValueError:
            address = None
        udp = protocol == PROTOCOL_QUIC
        for profile in self.profiles:
            via = self._match(profile, host, address, udp=udp, port=port, protocol=protocol)
            if via is None:
                continue
            if profile.orchestra_key:
                number = locked_number(self.locked.get(profile.orchestra_key) or {}, host)
                return Coverage(
                    profile.name or profile.orchestra_key.upper(),
                    via,
                    True,
                    orchestra=True,
                    locked_strategy=number,
                    strategy_text=strategy_text(profile.numbered.get(number, ())) if number else "",
                )
            named = profile.strategy and not profile.custom_strategy
            custom = profile.strategy and profile.custom_strategy
            return Coverage(
                profile.title(),
                via,
                profile.strategy,
                strategy_name=profile.named_strategy if named else "",
                custom_strategy=custom,
                preset=self.preset,
                strategy_text=strategy_text(profile.strategy_lines) if custom else "",
            )
        return Coverage(preset=self.preset, orchestra=any(profile.orchestra_key for profile in self.profiles))

    @staticmethod
    def _match(profile: _Profile, host: str, address, *, udp: bool, port: int, protocol: str) -> str | None:
        has_groups = bool(profile.tcp or profile.udp or profile.other_groups)
        if has_groups and not _ports_match(profile.udp if udp else profile.tcp, port):
            return None
        if profile.l7 and protocol not in profile.l7:
            return None
        via = ""
        if profile.ips or profile.ips_exclude:
            if address is None:
                return None
            if any(address in network for network in profile.ips_exclude):
                return None
            if profile.ips:
                if not any(address in network for network in profile.ips):
                    return None
                via = f"адрес в {', '.join(profile.ip_files)}"
        if profile.hosts_exclude.matches(host):
            return None
        if profile.hosts.entries:
            if profile.hosts.matches(host):
                return f"список {', '.join(profile.host_files)}"
            if not profile.autohostlist:
                return None
            return "автосписок (имя ещё не в нём)"
        if profile.autohostlist:
            return "автосписок (имя ещё не в нём)"
        return via or "все сайты"


def read_at_config(path: Path) -> list[str]:
    """Аргументы из файла ``@...``: по одному в строке, в кавычках shell."""
    args: list[str] = []
    for line in _read_lines(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            args.extend(shlex.split(line))
        except ValueError:
            args.append(line)
    return args


def _at_config_of(command_line: str) -> Path | None:
    try:
        parts = shlex.split(command_line, posix=False)
    except ValueError:
        parts = command_line.split()
    for part in parts[1:]:
        part = part.strip('"')
        if part.startswith("@") and len(part) > 1:
            return Path(part[1:].strip('"'))
    return None


def preset_file_of(config: Path, base_dir: Path) -> Path | None:
    """Файл пресета, из которого написан файл ``@…``; ``None`` — такого файла пресета нет."""
    digest = config.stem.rsplit("_", 1)[-1].lower()
    try:
        text = config.read_text(encoding="utf-8", errors="replace", newline="")
    except OSError:
        return None
    presets_root = base_dir / "presets"
    for candidate in sorted(presets_root.rglob("*.txt")) if presets_root.is_dir() else ():
        if "replaced" in candidate.relative_to(presets_root).parts[:1]:
            continue
        source = f"{os.path.abspath(str(candidate))}\0{text}".encode("utf-8", "surrogatepass")
        if hashlib.sha1(source).hexdigest()[:20] == digest:
            return candidate
    return None


def preset_name_of(config: Path, base_dir: Path) -> str:
    """Название пресета, из которого написан файл ``@…``; пусто — такого файла пресета нет."""
    found = preset_file_of(config, base_dir)
    return found.stem if found is not None else ""


def winws1_source_text(config: Path, preset: Path | None, base_dir: Path) -> str:
    """Текст пресета Zapret 1, если ``@…`` написан ровно из него; пусто — пресет с тех пор менялся.

    zapret1_runner пишет в ``@…`` полные пути к спискам и файлам фейков, а готовые стратегии
    в каталогах — с короткими. Сравнивать надо текст пресета, как на странице профилей, но
    только убедившись, что из него получается тот самый ``@…``.
    """
    from utils.args_resolver import resolve_args_paths
    from winws_runtime.runners.preset_runner_support import launch_args_from_preset_text

    if preset is None:
        return ""
    try:
        text = preset.read_text(encoding="utf-8", errors="replace")
        config_text = config.read_text(encoding="utf-8", errors="replace", newline="")
    except OSError:
        return ""
    args = resolve_args_paths(
        launch_args_from_preset_text(text),
        os.path.join(str(base_dir), "lists"),
        os.path.join(str(base_dir), "bin"),
        os.path.join(str(base_dir), "windivert.filter"),
    )
    rebuilt = "\n".join(shlex.quote(arg) for arg in args) + "\n" if args else ""
    return text if rebuilt == config_text else ""


def with_strategy_names(
    running: RunningPreset, preset_text: str, base_dir: Path, *, engine: str = ENGINE_WINWS2
) -> RunningPreset:
    """Названия стратегий профилей — тем же поиском, что на странице профилей.

    Профили разбирает парсер пресетов программы; если его профили не совпали с нашими по
    названиям и порядку, названий нет — гадать нельзя.
    """
    from core.paths import AppPaths
    from profile.derived_cache import basic_strategy_entries, resolve_strategy
    from profile.parser import parse_preset_text
    from profile.strategy_catalog import load_strategy_catalogs

    catalogs = load_strategy_catalogs(AppPaths(user_root=base_dir, local_root=base_dir), engine)
    if not catalogs:
        return running
    parsed = parse_preset_text(preset_text, engine=engine, source_name="running").profiles
    if [str(item.name or "") for item in parsed] != [profile.name for profile in running.profiles]:
        return running
    profiles = []
    for profile, item in zip(running.profiles, parsed):
        strategy_id, name = resolve_strategy(item, basic_strategy_entries(item, catalogs))
        if strategy_id == "none":
            profiles.append(profile)
        elif strategy_id == "custom":
            profiles.append(dataclasses.replace(profile, custom_strategy=True))
        else:
            profiles.append(dataclasses.replace(profile, named_strategy=str(name or "")))
    return dataclasses.replace(running, profiles=tuple(profiles))


def orchestra_locks(keys: Iterable[str]) -> dict[str, dict[str, object]]:
    """Закреплённые оркестратором стратегии по ключам его профилей (из настроек программы)."""
    from settings.store import get_orchestra_locked_map

    return {key: dict(get_orchestra_locked_map(key) or {}) for key in dict.fromkeys(keys) if key}


def running_preset(*, locks: Callable[[Iterable[str]], Mapping] = orchestra_locks) -> RunningPreset | None:
    """Пресет, с которым сейчас работает winws2 (или winws). ``None`` — не запущен или узнать не удалось."""
    try:
        from settings.mode import ALL_WINWS_EXE_NAME_SET
        from utils.windows_process_probe import iter_process_records_winapi, process_command_line
    except Exception:
        return None
    for pid, name in iter_process_records_winapi():
        if str(name or "").lower() not in ALL_WINWS_EXE_NAME_SET:
            continue
        config = _at_config_of(process_command_line(pid))
        if config is None or not config.is_file():
            continue
        return running_from_at_config(config, str(name), locks=locks)
    return None


def running_from_at_config(
    config: Path, exe_name: str, *, locks: Callable[[Iterable[str]], Mapping] = orchestra_locks
) -> RunningPreset:
    """Что запущено по файлу ``@…``: пресет (с названиями стратегий) или оркестратор (с закреплёнными)."""
    engine = ENGINE_WINWS2 if exe_name.lower().startswith("winws2") else ENGINE_WINWS1
    parents = config.resolve().parents
    args = read_at_config(config)
    if config.name.lower() == ORCHESTRA_AT_CONFIG_NAME:
        # <рабочая папка>/user/lua/circular-config.runtime.txt: пути списков — от рабочей папки.
        base_dir = parents[2] if len(parents) > 2 else config.parent
        running = parse_launch_args(args, base_dir, engine=engine)
        try:
            locked = dict(locks(profile.orchestra_key for profile in running.profiles))
        except Exception:  # noqa: BLE001 — без закреплённых строка скажет «ещё перебирает»
            locked = {}
        return dataclasses.replace(running, locked=locked)
    # Файл лежит в <рабочая папка>/user/tmp/winws2_at_config: пути списков — от рабочей папки.
    base_dir = parents[3] if len(parents) > 3 else config.parent
    preset = preset_file_of(config, base_dir)
    running = dataclasses.replace(
        parse_launch_args(args, base_dir, engine=engine), preset=preset.stem if preset is not None else ""
    )
    try:
        if engine == ENGINE_WINWS2:
            running = with_strategy_names(running, "\n".join(args), base_dir, engine=engine)
        elif source := winws1_source_text(config, preset, base_dir):
            running = with_strategy_names(running, source, base_dir, engine=engine)
    except Exception:  # noqa: BLE001 — без названия стратегии строка всё равно верна
        pass
    return running


def annotate(probes: Sequence, zapret_running: bool | None) -> None:
    """Zapret запущен, а адрес закрыт: записать в ``probe.coverage``, какой профиль пресета ловит это имя.

    Только для закрытых фильтром адресов (DPI, зависание, блок по адресу): чужой сертификат и
    «сайт не работает сам» к профилям не относятся. Узнать пресет не удалось — строки просто нет.
    """
    from diagnostics.verdict import ReachState

    if not zapret_running:
        return
    broken = [probe for probe in probes if probe.reach_state in (ReachState.DPI, ReachState.FREEZE, ReachState.IP_BLOCK)]
    if not broken:
        return
    try:
        running = running_preset()
    except Exception:  # noqa: BLE001 — без пресета отчёт обходится без строки «Профиль пресета»
        return
    if running is None:
        return
    for probe in broken:
        probe.coverage = running.covering(probe.host, probe.reach.ip if probe.reach is not None else "")
