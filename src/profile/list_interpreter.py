from __future__ import annotations

from dataclasses import dataclass

from .models import Profile, build_profile_logical_key
from .stock_names import current_stock_profile_name


@dataclass(frozen=True)
class ProfileListSource:
    key: str
    profile: Profile
    in_preset: bool
    order: int
    user_template_key: str = ""
    resolved_display_name: str = ""


def build_profile_list_sources(
    preset_profiles: tuple[Profile, ...],
    templates: dict[str, Profile],
) -> tuple[ProfileListSource, ...]:
    groups: list[list[ProfileListSource]] = []
    group_aliases: list[set[str]] = []
    alias_to_groups: dict[str, set[int]] = {}

    for profile in preset_profiles:
        source = ProfileListSource(
            key=profile.key,
            profile=profile,
            in_preset=True,
            order=profile.index,
        )
        _add_preset_source_to_groups(source, groups, group_aliases, alias_to_groups)

    template_order = len(preset_profiles)
    for template_id, profile in templates.items():
        source = ProfileListSource(
            key=f"template:{template_id}",
            profile=profile,
            in_preset=False,
            order=template_order + profile.index,
            user_template_key=f"template:{template_id}" if str(template_id).startswith("user:") else "",
        )
        _add_template_source_to_groups(source, groups, group_aliases, alias_to_groups)

    selected = [_select_source(candidates) for candidates in groups if candidates]
    selected.sort(key=lambda source: (source.order, source.resolved_display_name.lower(), source.key))
    return tuple(selected)


def _add_preset_source_to_groups(
    source: ProfileListSource,
    groups: list[list[ProfileListSource]],
    group_aliases: list[set[str]],
    alias_to_groups: dict[str, set[int]],
) -> None:
    aliases = _logical_profile_keys(source.profile)
    group_index = len(groups)
    groups.append([source])
    group_aliases.append(set(aliases))
    for alias in aliases:
        alias_to_groups.setdefault(alias, set()).add(group_index)


def _add_template_source_to_groups(
    source: ProfileListSource,
    groups: list[list[ProfileListSource]],
    group_aliases: list[set[str]],
    alias_to_groups: dict[str, set[int]],
) -> None:
    aliases = _logical_profile_keys(source.profile)
    existing = sorted({group_index for alias in aliases for group_index in alias_to_groups.get(alias, set())})
    preset_existing = [group_index for group_index in existing if _group_has_preset_source(groups[group_index])]
    if preset_existing:
        for group_index in preset_existing:
            groups[group_index].append(source)
            _remember_group_aliases(group_index, aliases, group_aliases, alias_to_groups)
        return
    _add_source_to_groups(source, groups, group_aliases, alias_to_groups)


def _add_source_to_groups(
    source: ProfileListSource,
    groups: list[list[ProfileListSource]],
    group_aliases: list[set[str]],
    alias_to_groups: dict[str, set[int]],
) -> None:
    aliases = _logical_profile_keys(source.profile)
    existing = sorted({group_index for alias in aliases for group_index in alias_to_groups.get(alias, set())})
    if not existing:
        group_index = len(groups)
        groups.append([source])
        group_aliases.append(set(aliases))
        for alias in aliases:
            alias_to_groups.setdefault(alias, set()).add(group_index)
        return

    target = existing[0]
    groups[target].append(source)
    group_aliases[target].update(aliases)

    for other in reversed(existing[1:]):
        if other == target or not groups[other]:
            continue
        groups[target].extend(groups[other])
        group_aliases[target].update(group_aliases[other])
        groups[other] = []
        group_aliases[other] = set()

    for alias in group_aliases[target]:
        alias_to_groups.setdefault(alias, set()).add(target)


def _group_has_preset_source(group: list[ProfileListSource]) -> bool:
    return any(source.in_preset for source in group)


def _remember_group_aliases(
    group_index: int,
    aliases: tuple[str, ...],
    group_aliases: list[set[str]],
    alias_to_groups: dict[str, set[int]],
) -> None:
    group_aliases[group_index].update(aliases)
    for alias in group_aliases[group_index]:
        alias_to_groups.setdefault(alias, set()).add(group_index)


def _logical_profile_keys(profile: Profile) -> tuple[str, ...]:
    keys: list[str] = []
    name = str(getattr(profile, "name", "") or "").strip()
    if name:
        # Профиль под прежним стоковым именем — тот же профиль, что шаблон с
        # нынешним именем: иначе в списке стояли бы оба.
        keys.append(f"name:{current_stock_profile_name(name).casefold()}")
    match_key = build_profile_logical_key(profile.match_signature)
    if match_key:
        keys.append(match_key)
    if not keys:
        keys.append(str(profile.match_signature or profile.key))
    return tuple(dict.fromkeys(keys))


def _logical_profile_key(profile: Profile) -> str:
    return _logical_profile_keys(profile)[0]


def _select_source(candidates: list[ProfileListSource]) -> ProfileListSource:
    user_template_key = next((source.user_template_key for source in candidates if source.user_template_key), "")
    preset_sources = [source for source in candidates if source.in_preset]
    if preset_sources:
        preset_sources.sort(key=lambda source: (not source.profile.enabled, source.profile.index))
        selected = preset_sources[0]
        return _source_with_resolved_display_name(selected, candidates, user_template_key=user_template_key)

    candidates.sort(key=lambda source: (_template_kind_rank(source.profile), source.order))
    selected = candidates[0]
    return _source_with_resolved_display_name(selected, candidates, user_template_key=user_template_key)


def _template_kind_rank(profile: Profile) -> int:
    if profile.match.hostlist_lines or profile.match.hostlist_exclude_lines:
        return 0
    if profile.match.ipset_lines or profile.match.ipset_exclude_lines:
        return 1
    return 2


def _source_with_resolved_display_name(
    selected: ProfileListSource,
    candidates: list[ProfileListSource],
    *,
    user_template_key: str = "",
) -> ProfileListSource:
    return ProfileListSource(
        key=selected.key,
        profile=selected.profile,
        in_preset=selected.in_preset,
        order=selected.order,
        user_template_key=user_template_key or selected.user_template_key,
        resolved_display_name=_resolved_display_name(selected, candidates),
    )


def _resolved_display_name(selected: ProfileListSource, candidates: list[ProfileListSource]) -> str:
    profile_name = _profile_name(selected.profile)
    if selected.in_preset and profile_name:
        # В пресете пользователя профиль может стоять под прежним стоковым
        # именем: показываем нынешнее, текст пресета не трогаем.
        return current_stock_profile_name(profile_name)
    if selected.in_preset:
        template_name = _first_template_profile_name(candidates)
        if template_name:
            return template_name
        return _profile_display_name(selected.profile)
    return profile_name or _profile_display_name(selected.profile)


def _first_template_profile_name(candidates: list[ProfileListSource]) -> str:
    return next(
        (
            _profile_name(source.profile)
            for source in candidates
            if not source.in_preset and _profile_name(source.profile)
        ),
        "",
    )


def _profile_name(profile: Profile) -> str:
    return str(getattr(profile, "name", "") or "").strip()


def _profile_display_name(profile: Profile) -> str:
    return str(getattr(profile, "display_name", "") or "").strip()
