"""Workflow изменений managed-списков orchestra UI."""

from __future__ import annotations

from dataclasses import dataclass

from log.log import log


@dataclass(frozen=True, slots=True)
class ManagedListActionResult:
    changed: bool
    restarted: bool = False
    count: int = 0


@dataclass(frozen=True, slots=True)
class BlockedStrategyItem:
    hostname: str
    strategy: int
    askey: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class BlockedListSnapshot:
    items: tuple[BlockedStrategyItem, ...]
    user_count: int
    default_count: int
    direct_blocked_by_askey: dict | None = None

    @property
    def total_count(self) -> int:
        return self.user_count + self.default_count

    @property
    def user_items(self) -> tuple[BlockedStrategyItem, ...]:
        return tuple(item for item in self.items if not item.is_default)

    @property
    def default_items(self) -> tuple[BlockedStrategyItem, ...]:
        return tuple(item for item in self.items if item.is_default)


@dataclass(frozen=True, slots=True)
class LockedStrategyItem:
    domain: str
    strategy: int
    askey: str


@dataclass(frozen=True, slots=True)
class LockedListSnapshot:
    items: tuple[LockedStrategyItem, ...]
    counts_by_askey: dict[str, int]
    direct_locked_by_askey: dict | None = None

    @property
    def total_count(self) -> int:
        return sum(self.counts_by_askey.values())

    @property
    def tcp_count(self) -> int:
        return sum(self.counts_by_askey.get(key, 0) for key in ("tls", "http", "mtproto"))

    @property
    def udp_count(self) -> int:
        return sum(self.counts_by_askey.get(key, 0) for key in ("quic", "discord", "wireguard", "dns", "stun", "unknown"))


def reload_blocked_snapshot(*, orchestra, runner, askey_all: tuple[str, ...]) -> BlockedListSnapshot:
    if runner and hasattr(runner, "blocked_manager"):
        runner.blocked_manager.load()
        log("Список заблокированных перезагружен из settings.sqlite3 (runner)", "INFO")
        return build_blocked_snapshot(orchestra=orchestra, runner=runner, direct_blocked_by_askey=None, askey_all=askey_all)

    temp_manager = orchestra.create_loaded_blocked_manager()
    direct = {askey: dict(temp_manager.blocked_by_askey[askey]) for askey in askey_all}
    total = sum(len(strategies) for askey_data in temp_manager.blocked_by_askey.values() for strategies in askey_data.values())
    log(f"Загружено напрямую из settings.sqlite3: {total} заблокированных стратегий", "INFO")
    return build_blocked_snapshot(orchestra=orchestra, runner=None, direct_blocked_by_askey=direct, askey_all=askey_all)


def build_blocked_snapshot(*, orchestra, runner, direct_blocked_by_askey: dict | None, askey_all: tuple[str, ...]) -> BlockedListSnapshot:
    blocked_manager = runner.blocked_manager if runner and hasattr(runner, "blocked_manager") else None
    blocked_data = blocked_manager.blocked_by_askey if blocked_manager is not None else direct_blocked_by_askey
    if blocked_data is None:
        return reload_blocked_snapshot(orchestra=orchestra, runner=None, askey_all=askey_all)

    items: list[BlockedStrategyItem] = []
    user_count = 0
    default_count = 0
    for askey in askey_all:
        for hostname, strategies in blocked_data.get(askey, {}).items():
            for strategy in strategies:
                if blocked_manager is not None:
                    is_default = bool(blocked_manager.is_default_blocked(hostname, strategy))
                else:
                    is_default = (
                        strategy == 1
                        and askey == "tls"
                        and bool(orchestra.is_default_blocked_pass_domain(hostname))
                    )
                if is_default:
                    default_count += 1
                else:
                    user_count += 1
                items.append(BlockedStrategyItem(hostname=hostname, strategy=strategy, askey=askey, is_default=is_default))

    items.sort(key=lambda item: (item.is_default, item.hostname.lower(), item.askey, item.strategy))
    return BlockedListSnapshot(
        items=tuple(items),
        user_count=user_count,
        default_count=default_count,
        direct_blocked_by_askey=direct_blocked_by_askey,
    )


def reload_locked_snapshot(*, orchestra, runner, askey_all: tuple[str, ...]) -> LockedListSnapshot:
    if runner and hasattr(runner, "locked_manager"):
        runner.locked_manager.load()
        log("Список залоченных перезагружен из settings.sqlite3 (runner)", "INFO")
        return build_locked_snapshot(orchestra=orchestra, runner=runner, direct_locked_by_askey=None, askey_all=askey_all)

    temp_manager = orchestra.create_loaded_locked_manager()
    direct = {askey: dict(temp_manager.locked_by_askey[askey]) for askey in askey_all}
    total = sum(len(strategies) for strategies in direct.values())
    log(f"Загружено напрямую из settings.sqlite3: {total} залоченных стратегий", "INFO")
    return build_locked_snapshot(orchestra=orchestra, runner=None, direct_locked_by_askey=direct, askey_all=askey_all)


def build_locked_snapshot(*, orchestra, runner, direct_locked_by_askey: dict | None, askey_all: tuple[str, ...]) -> LockedListSnapshot:
    _ = orchestra
    locked_data = runner.locked_manager.locked_by_askey if runner and hasattr(runner, "locked_manager") else direct_locked_by_askey
    if locked_data is None:
        return reload_locked_snapshot(orchestra=orchestra, runner=None, askey_all=askey_all)

    items: list[LockedStrategyItem] = []
    for askey in askey_all:
        for domain, strategy in locked_data.get(askey, {}).items():
            items.append(LockedStrategyItem(domain=domain, strategy=strategy, askey=askey))
    items.sort(key=lambda item: item.domain.lower())
    counts = {askey: len(locked_data.get(askey, {})) for askey in askey_all}
    return LockedListSnapshot(
        items=tuple(items),
        counts_by_askey=counts,
        direct_locked_by_askey=direct_locked_by_askey,
    )


def is_blocked_strategy(*, orchestra, runner, domain: str, strategy: int) -> bool:
    if runner and hasattr(runner, "blocked_manager"):
        return bool(runner.blocked_manager.is_blocked(domain, strategy))
    blocked_manager = orchestra.create_loaded_blocked_manager()
    return bool(blocked_manager.is_blocked(domain, strategy))


def change_blocked_strategy(runner, *, hostname: str, old_strategy: int, new_strategy: int, askey: str) -> ManagedListActionResult:
    if not runner or not hasattr(runner, "blocked_manager"):
        log("Не удалось изменить блокировку: оркестратор не запущен", "WARNING")
        return ManagedListActionResult(changed=False)

    runner.blocked_manager.unblock(hostname, old_strategy, askey)
    runner.blocked_manager.block(hostname, new_strategy, askey)
    log(f"Изменена блокировка: {hostname} [{askey.upper()}] #{old_strategy} -> #{new_strategy}", "INFO")
    return ManagedListActionResult(changed=True)


def add_blocked_strategy(runner, *, domain: str, strategy: int, askey: str) -> ManagedListActionResult:
    if not runner:
        return ManagedListActionResult(changed=False)

    runner.blocked_manager.block(domain, strategy, askey, user_block=True)
    log(f"Заблокирована стратегия #{strategy} для {domain} [{askey.upper()}]", "INFO")
    restarted = False
    if runner.is_running():
        runner.restart()
        restarted = True
    return ManagedListActionResult(changed=True, restarted=restarted)


def remove_blocked_strategy(runner, *, hostname: str, strategy: int, askey: str) -> ManagedListActionResult:
    if not runner:
        return ManagedListActionResult(changed=False)

    success = bool(runner.blocked_manager.unblock(hostname, strategy, askey))
    if not success:
        return ManagedListActionResult(changed=False)

    log(f"Разблокирована стратегия #{strategy} для {hostname} [{askey.upper()}]", "INFO")
    restarted = False
    if runner.is_running():
        runner.restart()
        restarted = True
    return ManagedListActionResult(changed=True, restarted=restarted)


def count_user_blocked_strategies(runner, *, askey_all: tuple[str, ...]) -> int:
    if not runner:
        return 0

    user_count = 0
    for askey in askey_all:
        for hostname, strategies in runner.blocked_manager.blocked_by_askey.get(askey, {}).items():
            for strategy in strategies:
                if not runner.blocked_manager.is_default_blocked(hostname, strategy):
                    user_count += 1
    return user_count


def clear_user_blocked_strategies(runner, *, user_count: int) -> ManagedListActionResult:
    if not runner or user_count <= 0:
        return ManagedListActionResult(changed=False, count=user_count)

    runner.blocked_manager.clear()
    log(f"Очищен пользовательский чёрный список ({user_count} записей)", "INFO")
    restarted = False
    if runner.is_running():
        runner.restart()
        restarted = True
    return ManagedListActionResult(changed=True, restarted=restarted, count=user_count)


def current_locked_strategy(*, runner, direct_locked_by_askey: dict, domain: str, askey: str) -> int:
    if runner and hasattr(runner, "locked_manager"):
        return runner.locked_manager.locked_by_askey.get(askey, {}).get(domain, 1)
    return direct_locked_by_askey.get(askey, {}).get(domain, 1)


def change_locked_strategy(
    *,
    orchestra,
    runner,
    direct_locked_by_askey: dict,
    domain: str,
    new_strategy: int,
    askey: str,
) -> ManagedListActionResult:
    if runner and hasattr(runner, "locked_manager"):
        runner.locked_manager.lock(domain, new_strategy, askey, user_lock=True)
        log(f"[USER] Изменена стратегия: {domain} [{askey.upper()}] -> #{new_strategy}", "INFO")
        restarted = _restart_runner_after_locked_change(runner)
        return ManagedListActionResult(changed=True, restarted=restarted)

    temp_manager = orchestra.create_loaded_locked_manager()
    temp_manager.lock(domain, new_strategy, askey, user_lock=True)
    if askey in direct_locked_by_askey:
        direct_locked_by_askey[askey][domain] = new_strategy
    log(f"[USER] Изменена стратегия (direct): {domain} [{askey.upper()}] -> #{new_strategy}", "INFO")
    return ManagedListActionResult(changed=True)


def add_locked_strategy(
    *,
    orchestra,
    runner,
    direct_locked_by_askey: dict,
    domain: str,
    strategy: int,
    askey: str,
) -> ManagedListActionResult:
    if runner and hasattr(runner, "locked_manager"):
        runner.locked_manager.lock(domain, strategy, askey, user_lock=True)
        log(f"[USER] Залочена стратегия #{strategy} для {domain} [{askey.upper()}]", "INFO")
        restarted = _restart_runner_after_locked_change(runner)
        return ManagedListActionResult(changed=True, restarted=restarted)

    temp_manager = orchestra.create_loaded_locked_manager()
    temp_manager.lock(domain, strategy, askey, user_lock=True)
    if askey in direct_locked_by_askey:
        direct_locked_by_askey[askey][domain] = strategy
    log(f"[USER] Залочена стратегия (direct) #{strategy} для {domain} [{askey.upper()}]", "INFO")
    return ManagedListActionResult(changed=True)


def remove_locked_strategy(
    *,
    orchestra,
    runner,
    direct_locked_by_askey: dict,
    domain: str,
    askey: str,
) -> ManagedListActionResult:
    if runner and hasattr(runner, "locked_manager"):
        runner.locked_manager.unlock(domain, askey)
        log(f"Разлочена стратегия для {domain} [{askey.upper()}]", "INFO")
        restarted = False
        if runner.is_running():
            runner.restart()
            restarted = True
        return ManagedListActionResult(changed=True, restarted=restarted)

    temp_manager = orchestra.create_loaded_locked_manager()
    temp_manager.unlock(domain, askey)
    if askey in direct_locked_by_askey and domain in direct_locked_by_askey[askey]:
        del direct_locked_by_askey[askey][domain]
    log(f"Разлочена стратегия (direct) для {domain} [{askey.upper()}]", "INFO")
    return ManagedListActionResult(changed=True)


def count_locked_strategies(runner) -> int:
    if not runner:
        return 0
    return sum(len(strategies) for strategies in runner.locked_manager.locked_by_askey.values())


def clear_locked_strategies(runner, *, askey_all: tuple[str, ...], total: int) -> ManagedListActionResult:
    if not runner or total <= 0:
        return ManagedListActionResult(changed=False, count=total)

    for askey in askey_all:
        for domain in list(runner.locked_manager.locked_by_askey.get(askey, {}).keys()):
            runner.locked_manager.unlock(domain, askey)
    log(f"Разлочены все {total} стратегий", "INFO")

    restarted = False
    if runner.is_running():
        runner.restart()
        restarted = True
    return ManagedListActionResult(changed=True, restarted=restarted, count=total)


def add_whitelist_domain(*, orchestra, runner, domain: str) -> ManagedListActionResult:
    changed = bool(orchestra.add_whitelist_domain(runner, domain))
    if changed:
        log(f"Добавлен в белый список: {domain}", "INFO")
    return ManagedListActionResult(changed=changed)


def remove_whitelist_domain(*, orchestra, runner, domain: str) -> ManagedListActionResult:
    changed = bool(orchestra.remove_whitelist_domain(runner, domain))
    if changed:
        log(f"Удалён из белого списка: {domain}", "INFO")
    return ManagedListActionResult(changed=changed)


def clear_whitelist_user_domains(*, orchestra, runner, user_domains: list[str]) -> ManagedListActionResult:
    removed = int(orchestra.clear_whitelist_user_domains(runner, user_domains))
    if removed:
        log(f"Очищены все пользовательские домены из белого списка ({removed})", "INFO")
    return ManagedListActionResult(changed=bool(removed), count=removed)


def _restart_runner_after_locked_change(runner) -> bool:
    if not runner.is_running():
        return False
    runner._generate_learned_lua()
    log("[USER] Перезапуск оркестратора для применения user lock...", "INFO")
    runner.restart()
    return True
