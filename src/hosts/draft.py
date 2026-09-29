"""Выбор страницы Hosts, который ещё не записан в файл.

Каждый щелчок сразу уходит в запись, но запись идёт в фоне: пока она не
закончилась, выбор живёт здесь, поверх последнего снимка файла. Когда
приходит свежий снимок, то, что уже записано, из черновика уходит само.
"""

from __future__ import annotations

from hosts.page_snapshot import HostsPageSnapshot


MIXED = "__mixed__"


class HostsDraft:
    """Изменения поверх снимка hosts. Сам ничего не пишет."""

    def __init__(self, snapshot: HostsPageSnapshot):
        self._snapshot = snapshot
        self._overrides: dict[str, str | None] = {}
        self._adobe: bool | None = None

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

    def has_user_changes(self) -> bool:
        return bool(self._overrides) or self._adobe is not None

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
        self._overrides.clear()
        self._adobe = None
        for service_name, profile_id in overrides.items():
            self.set(service_name, profile_id)
        if adobe is not None:
            self.set_adobe(adobe)


__all__ = ["MIXED", "HostsDraft"]
