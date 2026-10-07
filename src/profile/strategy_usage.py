"""Какие готовые стратегии уже стоят в готовых пресетах и как часто.

Готовых стратегий сотни, и сами по себе они равноценны только на вид. Знание
«что стоит пробовать первым» уже записано в готовых пресетах: если стратегию
поставили на YouTube в тринадцати пресетах, с неё и надо начинать на YouTube.

Считается из самих пресетов (они — точка истины), отдельного списка
«советуемых» нет. Сервис опознаётся по условиям профиля (файл списка, порты),
а не по названию: название человек может поменять.

Общая частота «в скольких пресетах встречается» намеренно не главная: служебный
профиль, который есть в каждом пресете, поднял бы свою стратегию на первое
место для всех сервисов. Главное — частота на том же сервисе, затем — на
скольких разных сервисах стратегия встречается.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from profile.derived_cache import catalog_name_for_profile, resolve_strategy_lines
from profile.models import build_profile_logical_key
from profile.parser import parse_preset_text


@dataclass(frozen=True)
class StrategyUsage:
    # В скольких готовых пресетах стратегия стоит на этом же сервисе.
    same_service: int = 0
    # На скольких разных сервисах она встречается в готовых пресетах.
    services: int = 0

    @property
    def recommended(self) -> bool:
        return self.same_service > 0


NO_USAGE = StrategyUsage()


@dataclass(frozen=True)
class BuiltinStrategyUsage:
    """Итог обхода готовых пресетов одного движка."""

    # {сервис: {(каталог, id стратегии): число пресетов}}
    by_service: dict[str, dict[tuple[str, str], int]]
    # {(каталог, id стратегии): число разных сервисов}
    services: dict[tuple[str, str], int]

    def for_profile(self, profile, catalog_name: str | None = None) -> dict[str, StrategyUsage]:
        """Частота стратегий каталога этого профиля: {id стратегии: частота}."""
        catalog = str(catalog_name or catalog_name_for_profile(profile) or "")
        same = self.by_service.get(service_key(profile), {})
        usage: dict[str, StrategyUsage] = {}
        for (entry_catalog, strategy_id), services in self.services.items():
            if entry_catalog != catalog:
                continue
            usage[strategy_id] = StrategyUsage(
                same_service=int(same.get((entry_catalog, strategy_id), 0)),
                services=int(services),
            )
        return usage


EMPTY_BUILTIN_USAGE = BuiltinStrategyUsage(by_service={}, services={})

_NOT_A_CATALOG_STRATEGY = frozenset({"none", "custom"})
_USAGE_CACHE: dict[tuple[str, str], tuple[tuple[object, ...], BuiltinStrategyUsage]] = {}


def service_key(profile) -> str:
    """Сервис профиля: его условия без различия hostlist/ipset."""
    return str(build_profile_logical_key(getattr(profile, "match_signature", "") or "") or "").strip()


def count_builtin_strategy_usage(preset_texts, *, engine: str, catalogs) -> BuiltinStrategyUsage:
    """Считает частоту по текстам готовых пресетов: [(имя файла, текст), ...]."""
    by_service: dict[str, dict[tuple[str, str], int]] = {}
    for source_name, text in preset_texts:
        try:
            preset = parse_preset_text(text, engine=engine, source_name=source_name)
        except Exception:
            continue
        # Один пресет — один голос за пару «сервис, стратегия».
        seen: set[tuple[str, tuple[str, str]]] = set()
        for profile in preset.profiles:
            if not profile.enabled:
                continue
            service = service_key(profile)
            catalog = catalog_name_for_profile(profile)
            if not service or not catalog:
                continue
            strategy_id, _name = resolve_strategy_lines(
                profile, catalogs.get(catalog, {}), getattr(profile.strategy, "strategy_lines", ()) or ()
            )
            if strategy_id in _NOT_A_CATALOG_STRATEGY:
                continue
            vote = (service, (catalog, strategy_id))
            if vote in seen:
                continue
            seen.add(vote)
            counts = by_service.setdefault(service, {})
            counts[vote[1]] = counts.get(vote[1], 0) + 1

    services: dict[tuple[str, str], int] = {}
    for counts in by_service.values():
        for strategy in counts:
            services[strategy] = services.get(strategy, 0) + 1
    return BuiltinStrategyUsage(by_service=by_service, services=services)


def load_builtin_strategy_usage(app_paths, engine: str, catalogs, catalogs_signature) -> BuiltinStrategyUsage:
    """Частота по готовым пресетам движка; пересчитывается, когда меняются файлы."""
    from profile.strategy_catalog import _tree_signature

    engine_key = str(engine or "").strip().lower()
    try:
        directory = Path(app_paths.engine_paths(engine_key).builtin_presets_dir)
    except Exception:
        return EMPTY_BUILTIN_USAGE
    cache_key = (str(directory), engine_key)
    signature = (_tree_signature(directory), catalogs_signature)
    cached = _USAGE_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        return cached[1]

    texts: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.txt")) if directory.exists() else ():
        try:
            texts.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    usage = count_builtin_strategy_usage(texts, engine=engine_key, catalogs=catalogs)
    _USAGE_CACHE[cache_key] = (signature, usage)
    return usage
