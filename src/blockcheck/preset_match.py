"""Какой профиль пресета движок возьмёт для сайта — первый подходящий сверху.

Когда проба через пресет сайт не открыла, хотя в подборе стратегия работала,
причина обычно в порядке: выше стоит профиль, который ловит сайт раньше
профиля сайта. Догадка «скорее всего» не нужна — правило движка известно, и
пресет можно пройти так же, как он (``dp_match`` в nfq2/desync.c, zapret2):

1. ``--filter-l7`` пропускает TLS (нет фильтра — пропускает всё);
2. порт TCP 443 подходит под ``--filter-tcp``; без фильтров портов — любой порт;
   профиль только с ``--filter-udp`` соединение TCP не берёт;
3. есть списки адресов (``--ipset``, ``--ipset-ip``) — адрес сайта в них
   и не в ``--ipset-exclude``;
4. есть ``--hostlist-auto`` — профиль берёт соединение, когда имя известно;
5. нет списков доменов — профиль берёт всё, что прошло шаги 1–3;
6. иначе имя сайта в списках доменов и не исключено ``--hostlist-exclude``.

Списки читаются так, как их читает движок: итоговый файл ``lists/<имя>``
(свои записи сверху встроенных), домен — ``HostlistIndex`` с «!» и «^».
Выключенные (``--skip``) профили пропускаются. Здесь нет сети и нет окна.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

__all__ = ["ProfileHit", "first_profile_for"]


@dataclass(frozen=True, slots=True)
class ProfileHit:
    # Номер профиля сверху (с 1) и его имя для человека.
    number: int
    title: str
    # Почему этот профиль берёт сайт — словами.
    reason: str
    # Это профиль самого сайта, созданный «Применить».
    own: bool = False


def _values(lines, option: str) -> list[str]:
    prefix = option.lower() + "="
    found = []
    for line in lines or ():
        text = str(line or "").strip()
        if text.lower().startswith(prefix):
            found += [part.strip().strip('"').strip("'") for part in text.split("=", 1)[1].split(",") if part.strip()]
    return found


def _port_ok(filter_lines, port: int) -> bool:
    tcp = _values(filter_lines, "--filter-tcp")
    if not tcp:
        return not _values(filter_lines, "--filter-udp")
    for token in tcp:
        low, _sep, high = token.partition("-")
        try:
            if int(low) <= port <= int(high or low):
                return True
        except ValueError:
            continue
    return False


def _l7_ok(filter_lines) -> bool:
    names = {name.lower() for name in _values(filter_lines, "--filter-l7")}
    return not names or bool(names & {"tls", "all", "known"})


class _Lists:
    """Файлы списков одного прохода: каждый читается один раз."""

    def __init__(self, lists_dir: str) -> None:
        self._dir = lists_dir
        self._hosts: dict[str, object] = {}
        self._nets: dict[str, list] = {}

    def _read(self, name: str) -> str:
        path = os.path.join(self._dir, os.path.basename(name.replace("\\", "/")))
        try:
            with open(path, encoding="utf-8", errors="replace") as source:
                return source.read()
        except OSError:
            return ""

    def hosts(self, name: str):
        from lists.core.hostlist_syntax import HostlistIndex

        if name not in self._hosts:
            self._hosts[name] = HostlistIndex.from_texts((name, self._read(name)))
        return self._hosts[name]

    def nets(self, name: str) -> list:
        if name not in self._nets:
            self._nets[name] = _networks(self._read(name).splitlines())
        return self._nets[name]


def _networks(items) -> list:
    nets = []
    for item in items:
        text = str(item or "").split("#", 1)[0].strip()
        if not text:
            continue
        try:
            nets.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return nets


def _in(address, nets) -> bool:
    return any(address.version == net.version and address in net for net in nets)


def _host_found(index, host: str):
    found = index.lookup(host)
    return found is not None and not found.entry.exception


def _check(profile, host: str, addresses, lists: _Lists, port: int) -> str:
    """Почему профиль берёт сайт; пусто — не берёт."""
    match = profile.match
    filters = match.filter_lines
    if not _l7_ok(filters) or not _port_ok(filters, port):
        return ""
    ipset_files = _values(match.ipset_lines, "--ipset")
    inline_nets = _networks(_values(match.inline_ipset_lines, "--ipset-ip"))
    exclude_files = _values(match.ipset_exclude_lines, "--ipset-exclude")
    excluded = _networks(_values(match.ipset_exclude_lines, "--ipset-exclude-ip")) + [net for name in exclude_files for net in lists.nets(name)]
    by_address = ""
    # Исключения адресов действуют и без списка адресов: «все, кроме этих» (IpsetCheck в движке).
    candidates = [address for address in addresses if not _in(address, excluded)]
    if excluded and addresses and not candidates:
        return ""
    if ipset_files or inline_nets:
        nets = inline_nets + [net for name in ipset_files for net in lists.nets(name)]
        hit = next((address for address in candidates if _in(address, nets)), None)
        if hit is None:
            return ""
        by_address = f"адрес {hit} есть в его списке адресов"
    if match.hostlist_auto_lines:
        return "у него автосписок доменов: такой профиль берёт любое соединение, когда имя сайта известно"
    hostlist_files = _values(match.hostlist_lines, "--hostlist")
    inline_domains = _values(match.hostlist_domains_lines, "--hostlist-domains")
    if not hostlist_files and not inline_domains:
        if by_address:
            return by_address
        if excluded:
            return f"у него нет списка сайтов — он берёт все сайты на порту {port}, кроме адресов из своих исключений"
        return f"у него нет списков сайтов и адресов — он берёт все сайты на порту {port}"
    from lists.core.hostlist_syntax import HostlistIndex

    exclude_index = HostlistIndex.from_texts(
        ("exclude-domains", "\n".join(_values(match.hostlist_exclude_lines, "--hostlist-exclude-domains"))),
        *((name, lists._read(name)) for name in _values(match.hostlist_exclude_lines, "--hostlist-exclude")),
    )
    if _host_found(exclude_index, host):
        return ""
    if inline_domains and _host_found(HostlistIndex.from_texts(("domains", "\n".join(inline_domains))), host):
        return by_address or f"{host} записан прямо в нём"
    for name in hostlist_files:
        if _host_found(lists.hosts(name), host):
            return by_address or f"{host} есть в его списке {os.path.basename(name)}"
    return ""


def first_profile_for(preset_text: str, host: str, addresses=(), *, lists_dir: str, port: int = 443) -> ProfileHit | None:
    """Первый сверху профиль, который движок возьмёт для ``host:port`` (TCP). None — ни один."""
    from profile.parser import infer_profile_display_name, parse_preset_text

    from blockcheck.strategy_scan_apply import _is_targets_own_profile
    from settings.mode import ENGINE_WINWS2

    preset = parse_preset_text(preset_text, engine=ENGINE_WINWS2, source_name="")
    parsed = []
    for address in addresses:
        try:
            parsed.append(ipaddress.ip_address(address))
        except ValueError:
            continue
    lists = _Lists(lists_dir)
    for number, profile in enumerate(preset.profiles, start=1):
        if not getattr(profile, "enabled", True):
            continue
        reason = _check(profile, host.lower(), parsed, lists, port)
        if reason:
            title = str(getattr(profile, "name", "") or "") or infer_profile_display_name(profile.match, profile.index)
            return ProfileHit(number, title, reason, _is_targets_own_profile(profile, host, port))
    return None
