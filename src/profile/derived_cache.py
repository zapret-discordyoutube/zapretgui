"""Контентно-производные данные профиля и их кэш.

Ядро (`ProfileDerivedCore`) считается только из текста профиля и каталогов стратегий,
поэтому ключуется по (engine, подпись каталогов, сырой текст и имя профиля,
отпечаток папки списков) и переживает
смену пресета/ревизии: неизменённый профиль не пересчитывается. Контекстные поля
(порядок, папка, enabled, rating) сюда не входят — их накладывает сборка списка.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import time
from typing import Any

from settings.mode import ENGINE_WINWS2

from .editable_settings import EditableProfileSettings, read_editable_profile_settings
from .filter_switch import resolve_filter_kind_switch
from .list_interpreter import build_profile_list_sources
from .match_filters import (
    ports_label_from_match_lines,
    protocol_label_from_match_lines,
    strategy_catalog_from_match_lines,
)
from .models import Preset, Profile
from .strategy_catalog import StrategyEntry
from .strategy_shape import composite_identity, strategy_shape


PROFILE_DERIVED_CACHE_LIMIT = 512


@dataclass(slots=True, frozen=True)
class ProfileDerivedCore:
    strategy_entries: dict[str, StrategyEntry]
    strategy_id: str
    strategy_name: str
    # Типы пакетов веток составной стратегии profile-а (пусто — обычная).
    strategy_payload_scopes: tuple[str, ...]
    list_type: str
    match_summary: str
    raw_profile_text: str
    editable: EditableProfileSettings
    editable_filter_kinds: tuple[str, ...]


def build_profile_derived_core(
    profile: Profile,
    *,
    catalogs: dict[str, dict[str, StrategyEntry]],
    app_paths,
    raw_profile_text: str | None = None,
) -> ProfileDerivedCore:
    raw_text = profile_raw_text(profile) if raw_profile_text is None else raw_profile_text
    strategy_entries = basic_strategy_entries(profile, catalogs)
    strategy_id, strategy_name = resolve_strategy(profile, strategy_entries)
    editable = read_editable_profile_settings(profile)
    # Проверки файлов hostlist/ipset — единственный диск в ядре; считаем один раз
    # и переиспользуем для list_type и editable_filter_kinds.
    filter_kinds = available_filter_kinds(editable, app_paths)
    list_type = _visible_list_type_for_kinds(profile, filter_kinds)
    match_summary = profile_match_summary(profile, list_type=list_type)
    return ProfileDerivedCore(
        strategy_entries=strategy_entries,
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        strategy_payload_scopes=profile_strategy_payload_scopes(profile),
        list_type=list_type,
        match_summary=match_summary,
        raw_profile_text=raw_text,
        editable=editable,
        editable_filter_kinds=filter_kinds,
    )


_LISTS_SIGNATURE_TTL_SEC = 0.5


class ProfileDerivedCache:
    """LRU контентных ядер. Не потокобезопасен: вызывающий держит лок сервиса."""

    def __init__(self, limit: int = PROFILE_DERIVED_CACHE_LIMIT) -> None:
        self._entries: OrderedDict[tuple[object, ...], ProfileDerivedCore] = OrderedDict()
        self._limit = max(1, int(limit))
        self._lists_signature_value: tuple[object, ...] = ()
        self._lists_signature_at = -1.0

    def forget_lists_signature(self) -> None:
        self._lists_signature_at = -1.0

    def _lists_signature(self, app_paths) -> tuple[object, ...]:
        """Отпечаток папки lists и её подпапок: mtime меняется при создании и
        удалении файла списка. Запоминается на полсекунды — сборка списка
        из сотни профилей не должна опрашивать диск сотню раз."""
        now = time.monotonic()
        if 0.0 <= now - self._lists_signature_at < _LISTS_SIGNATURE_TTL_SEC:
            return self._lists_signature_value
        lists_root = Path(str(getattr(app_paths, "user_root", "") or "")) / "lists"
        signature: list[object] = []
        try:
            signature.append(lists_root.stat().st_mtime_ns)
            with os.scandir(lists_root) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        signature.append((entry.name, entry.stat(follow_symlinks=False).st_mtime_ns))
        except OSError:
            signature.append(None)
        self._lists_signature_value = tuple(sorted(signature, key=repr))
        self._lists_signature_at = now
        return self._lists_signature_value

    def core_for(
        self,
        profile: Profile,
        *,
        catalogs: dict[str, dict[str, StrategyEntry]],
        catalogs_signature: tuple[object, ...],
        app_paths,
    ) -> ProfileDerivedCore:
        raw_text = profile_raw_text(profile)
        # Имя профиля не входит в raw_text (оно в «--new=Имя»), а от него
        # зависит роль «исключения»; доступность hostlist/ipset зависит от
        # файлов списков на диске. Без них ядро оставалось бы устаревшим.
        cache_key = (
            profile.engine,
            catalogs_signature,
            raw_text,
            str(profile.name or ""),
            str(profile.display_name or ""),
            self._lists_signature(app_paths),
        )
        cached = self._entries.get(cache_key)
        if cached is not None:
            self._entries.move_to_end(cache_key)
            return cached
        core = build_profile_derived_core(
            profile,
            catalogs=catalogs,
            app_paths=app_paths,
            raw_profile_text=raw_text,
        )
        self._entries[cache_key] = core
        while len(self._entries) > self._limit:
            self._entries.popitem(last=False)
        return core


class PresetSourcesCache:
    """Sources пресета, посчитанные один раз на (ревизию, объект пресета, объект шаблонов).

    Идентичность объекта пресета в ключе покрывает нормализацию: она заменяет
    снапшот пресета при неизменной ревизии файла. Идентичность словаря шаблонов
    покрывает пользовательские профили: кэш шаблонов ключуется ревизией
    user_profiles, поэтому любая их запись даёт новый объект словаря.
    """

    def __init__(self) -> None:
        self._entry: tuple[tuple[object, ...], Preset, dict[str, Profile], tuple[Any, ...]] | None = None

    def sources_for(
        self,
        preset_revision: tuple[object, ...],
        preset: Preset,
        templates: dict[str, Profile],
    ) -> tuple[Any, ...]:
        entry = self._entry
        if entry is not None and entry[0] == preset_revision and entry[1] is preset and entry[2] is templates:
            return entry[3]
        sources = tuple(build_profile_list_sources(tuple(preset.profiles), templates))
        self._entry = (preset_revision, preset, templates, sources)
        return sources

    def clear(self) -> None:
        self._entry = None


def basic_strategy_entries(profile: Profile, catalogs: dict[str, dict[str, StrategyEntry]]) -> dict[str, StrategyEntry]:
    if profile_list_type(profile) == "custom":
        return {}
    return dict(catalogs.get(catalog_name_for_profile(profile)) or {})


CUSTOM_STRATEGY_NAME = "Своя стратегия"


def resolve_strategy(profile: Profile, entries: dict[str, StrategyEntry]) -> tuple[str, str]:
    return resolve_strategy_lines(profile, entries, getattr(profile.strategy, "strategy_lines", ()) or ())


@lru_cache(maxsize=4096)
def _entry_identity_lines(engine: str, args: str) -> tuple[str, ...]:
    """Identity-строки записи каталога.

    Зависят только от (engine, текст args) и без кэша пересчитывались бы для
    каждой пары «профиль × запись каталога» — O(профили × каталог) splitlines
    на каждую сборку списка.
    """
    normalized = normalize_lines(args.splitlines())
    if engine != ENGINE_WINWS2:
        return normalized
    return tuple(line for line in normalized if line.lower().startswith("--lua-desync="))


@lru_cache(maxsize=4096)
def _entry_composite_identity(args: str) -> tuple[tuple[str, str], ...]:
    """Отпечаток составной записи каталога (кэш по тексту, как у обычных)."""
    return composite_identity(strategy_shape(args.splitlines()).body_lines)


def resolve_strategy_lines(profile: Profile, entries: dict[str, StrategyEntry], lines) -> tuple[str, str]:
    """Готовая стратегия, которой равны строки стратегии profile-а.

    winws2: составная стратегия profile-а (несколько веток ``--payload``)
    сравнивается целиком только с составными записями каталога, обычная — по
    строкам ``--lua-desync`` только с обычными записями
    (см. ``profile.strategy_shape``).
    """
    if profile.engine != ENGINE_WINWS2:
        current = normalize_lines(lines)
        if not current:
            return "none", "Стратегия не выбрана"
        matches = [entry for entry in entries.values() if _entry_identity_lines(profile.engine, entry.args) == current]
    else:
        shape = strategy_shape(lines)
        if not shape.lua_lines:
            return "none", "Стратегия не выбрана"
        if shape.composite:
            current_composite = composite_identity(shape.body_lines)
            matches = [
                entry
                for entry in entries.values()
                if entry.is_composite and _entry_composite_identity(entry.args) == current_composite
            ]
        else:
            current = shape.lua_lines
            matches = [
                entry
                for entry in entries.values()
                if not entry.is_composite and _entry_identity_lines(profile.engine, entry.args) == current
            ]
    if len(matches) == 1:
        return matches[0].strategy_id, matches[0].name
    return "custom", CUSTOM_STRATEGY_NAME


def profile_strategy_shape(profile: Profile):
    """Форма стратегии profile-а winws2 по порядку строк в пресете."""
    return strategy_shape(getattr(profile.strategy, "strategy_lines", ()) or ())


def profile_strategy_payload_scopes(profile: Profile) -> tuple[str, ...]:
    if profile.engine != ENGINE_WINWS2:
        return ()
    shape = profile_strategy_shape(profile)
    return shape.payload_scopes if shape.composite else ()


def normalize_lines(lines) -> tuple[str, ...]:
    return tuple(str(line or "").strip() for line in lines if str(line or "").strip())


def strategy_identity_lines(profile: Profile, lines) -> tuple[str, ...]:
    normalized = normalize_lines(lines)
    if profile.engine != ENGINE_WINWS2:
        return normalized
    return tuple(line for line in normalized if line.lower().startswith("--lua-desync="))


def catalog_name_for_profile(profile: Profile) -> str:
    return strategy_catalog_from_match_lines(tuple(profile.match.all_lines()))


def profile_list_type(profile: Profile) -> str:
    catalog_name = catalog_name_for_profile(profile)
    if catalog_name == "voice":
        return "voice"
    has_hostlist = bool(profile.match.hostlist_lines or profile.match.hostlist_domains_lines)
    has_ipset = bool(profile.match.ipset_lines or profile.match.inline_ipset_lines)
    has_excludes = bool(profile.match.hostlist_exclude_lines or profile.match.ipset_exclude_lines)
    if has_excludes:
        settings = read_editable_profile_settings(profile)
        if settings.filter_role == "exclude" and not (has_hostlist or has_ipset):
            if settings.filter_kind in {"hostlist", "ipset"}:
                return settings.filter_kind
        return "custom"
    if has_hostlist and has_ipset:
        return "custom"
    if has_hostlist:
        return "hostlist"
    if has_ipset:
        return "ipset"
    return catalog_name


def visible_list_type(profile: Profile, app_paths) -> str:
    settings = read_editable_profile_settings(profile)
    return _visible_list_type_for_kinds(profile, available_filter_kinds(settings, app_paths))


def _visible_list_type_for_kinds(profile: Profile, filter_kinds: tuple[str, ...]) -> str:
    list_type = profile_list_type(profile)
    if list_type not in {"hostlist", "ipset"}:
        return ""
    available = {kind for kind in filter_kinds if kind in {"hostlist", "ipset"}}
    if len(available) <= 1:
        return ""
    return list_type


def profile_has_filter_choice(profile: Profile, app_paths) -> bool:
    settings = read_editable_profile_settings(profile)
    if not settings.filter_editable:
        return False
    available = {
        kind
        for kind in available_filter_kinds(settings, app_paths)
        if kind in {"hostlist", "ipset"}
    }
    return len(available) > 1


def profile_match_summary(profile: Profile, *, list_type: str | None = None) -> str:
    match_lines = tuple(profile.match.all_lines())
    visible = profile_list_type(profile) if list_type is None else str(list_type or "")
    parts = [
        part
        for part in (protocol_label_from_match_lines(match_lines), ports_label_from_match_lines(match_lines), visible)
        if part
    ]
    return " • ".join(parts) or "без явных условий"


def available_filter_kinds(settings: EditableProfileSettings, app_paths) -> tuple[str, ...]:
    current_kind = str(settings.filter_kind or "hostlist").strip().lower()
    if not settings.filter_editable or current_kind not in {"hostlist", "ipset"}:
        return (current_kind,)

    result: list[str] = []
    for candidate in ("hostlist", "ipset"):
        if resolve_filter_kind_switch(settings, candidate, app_paths).allowed:
            result.append(candidate)
    return tuple(result) or (current_kind,)


def profile_raw_text(profile: Profile) -> str:
    return "\n".join(segment.text for segment in profile.segments if str(segment.text or "").strip()).strip()


__all__ = [
    "PROFILE_DERIVED_CACHE_LIMIT",
    "PresetSourcesCache",
    "ProfileDerivedCache",
    "ProfileDerivedCore",
    "available_filter_kinds",
    "basic_strategy_entries",
    "build_profile_derived_core",
    "catalog_name_for_profile",
    "normalize_lines",
    "profile_has_filter_choice",
    "profile_list_type",
    "profile_match_summary",
    "profile_raw_text",
    "CUSTOM_STRATEGY_NAME",
    "profile_strategy_payload_scopes",
    "profile_strategy_shape",
    "resolve_strategy",
    "resolve_strategy_lines",
    "strategy_identity_lines",
    "visible_list_type",
]
