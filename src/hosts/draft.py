"""Черновик изменений страницы Hosts.

Пока пользователь щёлкает переключатели, меняется только черновик. В файл
hosts всё записывается одной операцией по кнопке «Применить», а до этого
черновик умеет показать точные строки, которые добавятся и удалятся.
"""

from __future__ import annotations

from dataclasses import dataclass

from hosts.hosts_blocks import BLOCK_USER, BLOCK_ZAPRETGUI, mapping_domains
from hosts.ipv6_detection import is_ipv6_address
from hosts.page_snapshot import HostsPageSnapshot


MIXED = "__mixed__"


@dataclass(frozen=True, slots=True)
class HostsDraftPreview:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    # Ручные строки пользователя с теми же доменами: они останутся в файле,
    # но Windows возьмёт адрес из блока ZapretGUI, он стоит выше.
    shadowed: tuple[str, ...]


def build_block_rows(
    selection: dict[str, str],
    rows_by_service_profile: dict[tuple[str, str], tuple[tuple[str, str], ...]],
    *,
    ipv6_available: bool,
) -> list[tuple[str, str]]:
    """Строки блока ZapretGUI для выбора — те же правила, что у HostsManager.

    Повтор домена у разных сервисов: побеждает сервис, выбранный позже.
    """
    selected_by_domain: dict[str, tuple[str, list[str]]] = {}
    domain_order: list[str] = []
    for service_name, profile_id in selection.items():
        per_service: dict[str, tuple[str, list[str]]] = {}
        per_service_order: list[str] = []
        for domain, ip in rows_by_service_profile.get((service_name, profile_id), ()):
            domain_key = domain.casefold()
            item = per_service.get(domain_key)
            if item is None:
                per_service[domain_key] = (domain, [ip])
                per_service_order.append(domain_key)
            elif ip.casefold() not in {value.casefold() for value in item[1]}:
                item[1].append(ip)
        for domain_key in per_service_order:
            if domain_key not in selected_by_domain:
                domain_order.append(domain_key)
            selected_by_domain[domain_key] = per_service[domain_key]

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for domain_key in domain_order:
        domain, ips = selected_by_domain[domain_key]
        for ip in ips:
            if is_ipv6_address(ip) and not ipv6_available:
                continue
            key = (domain_key, ip.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append((domain, ip))
    return result


class HostsDraft:
    """Изменения поверх снимка hosts. Сам ничего не пишет."""

    def __init__(self, snapshot: HostsPageSnapshot):
        self._snapshot = snapshot
        self._overrides: dict[str, str | None] = {}
        self._adobe: bool | None = None
        self._stale_lines = _stale_block_lines(snapshot)

    @property
    def snapshot(self) -> HostsPageSnapshot:
        return self._snapshot

    # ── значения ──────────────────────────────────────────────

    def value(self, service_name: str) -> str | None:
        if service_name in self._overrides:
            return self._overrides[service_name]
        entry = self._snapshot.service(service_name)
        return entry.current if entry is not None else None

    def is_changed(self, service_name: str) -> bool:
        return service_name in self._overrides

    def set(self, service_name: str, profile_id: str | None) -> bool:
        """Меняет выбор одного сервиса. Возвращает True, если черновик изменился."""
        entry = self._snapshot.service(service_name)
        if entry is None or entry.unavailable_reason:
            return False
        if profile_id is not None and profile_id not in entry.profiles:
            return False
        before = self.value(service_name)
        if profile_id == entry.current:
            self._overrides.pop(service_name, None)
        else:
            self._overrides[service_name] = profile_id
        return before != profile_id

    def set_all_dns(self, profile_id: str | None) -> tuple[int, list[str]]:
        """Ставит профиль всем DNS-сервисам. Возвращает (изменено, пропущено)."""
        changed = 0
        skipped: list[str] = []
        for entry in self._snapshot.services:
            if entry.is_direct or entry.unavailable_reason:
                continue
            if profile_id is not None and profile_id not in entry.profiles:
                skipped.append(entry.name)
                continue
            if self.set(entry.name, profile_id):
                changed += 1
        return changed, skipped

    def dns_common_value(self) -> str | None:
        """Общий профиль всех DNS-сервисов, None — все выключены, MIXED — по-разному."""
        values = {
            self.value(entry.name)
            for entry in self._snapshot.services
            if not entry.is_direct and not entry.unavailable_reason
        }
        if len(values) == 1:
            return next(iter(values))
        return MIXED

    @property
    def adobe(self) -> bool:
        return self._snapshot.adobe_active if self._adobe is None else self._adobe

    def set_adobe(self, enabled: bool) -> bool:
        before = self.adobe
        self._adobe = None if bool(enabled) == self._snapshot.adobe_active else bool(enabled)
        return before != self.adobe

    @property
    def adobe_changed(self) -> bool:
        return self._adobe is not None

    # ── состояние черновика ───────────────────────────────────

    @property
    def stale_lines(self) -> tuple[str, ...]:
        """Лишние строки блока ZapretGUI: их нет ни у одного включённого сервиса.

        Например, сервис убрали из каталога. «Применить» пересобирает блок из
        выбора, поэтому такие строки уйдут при записи — черновик считается
        изменённым, даже если пользователь ничего не трогал.
        """
        return self._stale_lines

    def has_user_changes(self) -> bool:
        return bool(self._overrides) or self._adobe is not None

    def is_dirty(self) -> bool:
        return self.has_user_changes() or bool(self._stale_lines)

    def changed_services(self) -> list[str]:
        return [entry.name for entry in self._snapshot.services if entry.name in self._overrides]

    def selection(self) -> dict[str, str]:
        """Полный итоговый выбор «сервис → профиль» в порядке каталога."""
        result: dict[str, str] = {}
        for entry in self._snapshot.services:
            profile_id = self.value(entry.name)
            if profile_id:
                result[entry.name] = profile_id
        return result

    def reset(self) -> None:
        self._overrides.clear()
        self._adobe = None

    def rebase(self, snapshot: HostsPageSnapshot) -> None:
        """Переносит черновик на свежий снимок, выбрасывая то, что уже совпало."""
        overrides = dict(self._overrides)
        adobe = self._adobe
        self._snapshot = snapshot
        self._stale_lines = _stale_block_lines(snapshot)
        self._overrides.clear()
        self._adobe = None
        for service_name, profile_id in overrides.items():
            self.set(service_name, profile_id)
        if adobe is not None:
            self.set_adobe(adobe)

    # ── предпросмотр ──────────────────────────────────────────

    def preview(self) -> HostsDraftPreview:
        snapshot = self._snapshot
        new_rows = build_block_rows(
            self.selection(),
            snapshot.rows,
            ipv6_available=snapshot.ipv6_available,
        )
        new_lines = [f"{ip} {domain}" for domain, ip in new_rows]
        current_block = snapshot.block(BLOCK_ZAPRETGUI)
        current_lines = list(current_block.lines) if current_block is not None else []

        current_keys = {_line_key(line) for line in current_lines}
        new_keys = {_line_key(line) for line in new_lines}
        added = tuple(line for line in new_lines if _line_key(line) not in current_keys)
        removed = tuple(line for line in current_lines if _line_key(line) not in new_keys)

        new_domains = {domain.casefold() for domain, _ip in new_rows}
        user_block = snapshot.block(BLOCK_USER)
        shadowed = tuple(
            line
            for line in (user_block.lines if user_block is not None else ())
            if any(domain.casefold() in new_domains for domain in mapping_domains(line))
        )
        return HostsDraftPreview(added=added, removed=removed, shadowed=shadowed)


def _line_key(line: str) -> str:
    return " ".join(line.partition("#")[0].split()).casefold()


def _stale_block_lines(snapshot: HostsPageSnapshot) -> tuple[str, ...]:
    """Строки блока ZapretGUI, которых не будет при записи того же выбора."""
    block = snapshot.block(BLOCK_ZAPRETGUI)
    if block is None:
        return ()
    selection = {entry.name: entry.current for entry in snapshot.services if entry.current}
    expected = {
        _line_key(f"{ip} {domain}")
        for domain, ip in build_block_rows(selection, snapshot.rows, ipv6_available=snapshot.ipv6_available)
    }
    result: list[str] = []
    for line in block.lines:
        if _line_key(line) in expected:
            continue
        ip = line.partition("#")[0].split()[0]
        if not snapshot.ipv6_available and is_ipv6_address(ip):
            # IPv6 мог просто ещё не подняться: такие строки лишними не считаем.
            continue
        result.append(line)
    return tuple(result)


__all__ = ["MIXED", "HostsDraft", "HostsDraftPreview", "build_block_rows"]
