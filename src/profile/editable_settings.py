from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PureWindowsPath

from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2

from .models import Preset, Profile, ProfileSegment
from .parser import parse_preset_text
from .serializer import serialize_preset
from .winws2_transport import parse_out_range_expression


_SERVICE_EXCLUDE_LIST_NAMES = frozenset(
    {
        "ipset-ru.txt",
        "ipset-dns.txt",
        "ipset-exclude.txt",
        "netrogat.txt",
    }
)
_LIST_FILE_EXTENSIONS = (".txt", ".lst", ".list")


@dataclass(frozen=True)
class EditableProfileSettings:
    filter_kind: str = "hostlist"
    filter_value: str = ""
    filter_editable: bool = True
    filter_role: str = "primary"
    filter_protocol: str = ""
    in_range: str = "x"
    out_range: str = "a"


_DEFAULT_RANGE_BY_OPTION = {"--in-range": "x", "--out-range": "a"}


def read_editable_profile_settings(profile: Profile) -> EditableProfileSettings:
    in_range, out_range = read_profile_ranges(profile)
    return EditableProfileSettings(
        filter_kind=_editable_filter_kind(profile),
        filter_value=_editable_filter_value(profile),
        filter_editable=_editable_filter_is_file_based(profile),
        filter_role=_editable_filter_role(profile),
        filter_protocol=_editable_filter_protocol(profile),
        in_range=in_range,
        out_range=out_range,
    )


def read_profile_ranges(profile: Profile) -> tuple[str, str]:
    """Диапазоны ``--in-range``/``--out-range`` profile-а (по умолчанию x/a).

    Это настройки profile-а: значения, которые действуют на первую
    ``--lua-desync`` (в zapret2 внутрипрофильный фильтр действует с места
    указания до переопределения). Диапазоны после первой ``--lua-desync``
    принадлежат составной стратегии и здесь не читаются. Без стратегии —
    значение после всех фильтров profile: так оно подействует на стратегию,
    добавленную в конец.
    """
    if profile.engine != ENGINE_WINWS2:
        return _DEFAULT_RANGE_BY_OPTION["--in-range"], _DEFAULT_RANGE_BY_OPTION["--out-range"]
    start = _first_strategy_line_index(profile.segments)
    return (
        _effective_range(profile.segments, start, "--in-range"),
        _effective_range(profile.segments, start, "--out-range"),
    )


def with_editable_profile_settings(
    preset: Preset,
    profile_index: int,
    settings: EditableProfileSettings,
) -> Preset:
    """Меняет только то, что отличается от текущих настроек profile.

    Фильтр-список правится, только если изменились тип/значение, и при этом
    заменяется лишь редактируемая группа строк (остальные hostlist/ipset
    profile не трогаются). Диапазоны — настройки profile-а: правится строка
    до первой ``--lua-desync`` (или добавляется прямо перед ней), а строки
    внутри составной стратегии не трогаются.
    """
    if preset.engine not in {ENGINE_WINWS1, ENGINE_WINWS2}:
        raise ValueError(f"Unsupported profile preset engine: {preset.engine}")

    updated = deepcopy(preset)
    profile = updated.profiles[int(profile_index)]
    current = read_editable_profile_settings(profile)
    filter_kind = str(settings.filter_kind or "").strip().lower()
    if filter_kind not in {"hostlist", "ipset"}:
        raise ValueError("filter_kind must be hostlist or ipset")
    filter_role = str(settings.filter_role or "primary").strip().lower()
    if filter_role not in {"primary", "exclude"}:
        filter_role = current.filter_role

    if current.filter_editable:
        filter_value = normalize_filter_value(settings.filter_value, filter_kind, filter_role=filter_role)
        if not filter_value:
            raise ValueError("filter_value must not be empty")
        if not filter_value_is_file_reference(filter_value):
            raise ValueError("filter_value must point to a list file")
        if (filter_kind, filter_value, filter_role) != (current.filter_kind, current.filter_value, current.filter_role):
            filter_lines = [
                f"--{_filter_option_name(filter_kind, filter_role)}={value}"
                for value in _split_filter_values(filter_value)
            ]
            profile.segments = _replace_match_group(
                profile.segments,
                _match_group_names(current.filter_kind, current.filter_role),
                filter_lines,
            )

    if preset.engine == ENGINE_WINWS2:
        for option_name, value in (("--in-range", settings.in_range), ("--out-range", settings.out_range)):
            profile.segments = _with_profile_range(profile.segments, option_name, value)

    return _reparse(updated)


def with_editable_profile(profile: Profile, settings: EditableProfileSettings) -> Profile:
    preset = Preset(
        engine=profile.engine,
        header_lines=[],
        preamble_lines=[],
        profiles=[deepcopy(profile)],
    )
    return with_editable_profile_settings(preset, 0, settings).profiles[0]


def _editable_filter_kind(profile: Profile) -> str:
    if profile.match.hostlist_lines:
        return "hostlist"
    if profile.match.ipset_lines:
        return "ipset"
    if profile.match.hostlist_exclude_lines:
        return "hostlist"
    if profile.match.ipset_exclude_lines:
        return "ipset"
    if profile.match.hostlist_domains_lines:
        return "hostlist-domains"
    if profile.match.inline_ipset_lines:
        return "ipset-ip"
    return "hostlist"


def _editable_filter_value(profile: Profile) -> str:
    # Значение берётся из той же группы строк, что и тип фильтра: правка
    # заменяет именно эту группу, и проверка после записи читает её же.
    kind = _editable_filter_kind(profile)
    role = _editable_filter_role(profile)
    group_lines = {
        ("hostlist", "primary"): profile.match.hostlist_lines,
        ("ipset", "primary"): profile.match.ipset_lines,
        ("hostlist", "exclude"): profile.match.hostlist_exclude_lines,
        ("ipset", "exclude"): profile.match.ipset_exclude_lines,
        ("hostlist-domains", "primary"): profile.match.hostlist_domains_lines,
        ("ipset-ip", "primary"): profile.match.inline_ipset_lines,
    }.get((kind, role), ())
    group_values = [line.split("=", 1)[1].strip() for line in group_lines if "=" in line]
    if group_values:
        return ",".join(group_values)
    for lines in (
        profile.match.hostlist_lines,
        profile.match.hostlist_domains_lines,
        profile.match.ipset_lines,
        profile.match.inline_ipset_lines,
        profile.match.hostlist_exclude_lines,
        profile.match.ipset_exclude_lines,
    ):
        values = [line.split("=", 1)[1].strip() for line in lines if "=" in line]
        if values:
            return ",".join(values)
    return ""


def _editable_filter_is_file_based(profile: Profile) -> bool:
    if _editable_filter_role(profile) == "exclude":
        return True
    for segment in profile.segments:
        name = str(segment.name or "").strip().lower()
        if segment.kind == "match" and name in {"--hostlist", "--ipset", "--hostlist-exclude", "--ipset-exclude"}:
            return True
        if segment.kind == "match" and name in {
            "--hostlist-domains",
            "--ipset-ip",
            "--hostlist-exclude-domains",
            "--ipset-exclude-ip",
        }:
            return False
    return False


def _editable_filter_role(profile: Profile) -> str:
    if _is_service_exclusion_profile(profile):
        return "exclude"
    return "primary"


def _editable_filter_protocol(profile: Profile) -> str:
    has_udp = False
    has_tcp = False
    for line in profile.match.filter_lines:
        lowered = str(line or "").strip().lower()
        if lowered.startswith("--filter-udp="):
            has_udp = True
        elif lowered.startswith("--filter-tcp="):
            has_tcp = True
    if has_udp and not has_tcp:
        return "udp"
    if has_tcp and not has_udp:
        return "tcp"
    return ""


def new_profile_insert_index(preset: Preset) -> int:
    """Куда добавить новый profile: перед первым profile-исключением RU или
    profile-ом «на всё», иначе в конец.

    Profile-ы zapret2 проверяются по порядку и срабатывает первый подошедший.
    Исключения RU (hostlist/ipset из служебных списков) и profile без
    собственного hostlist/ipset перехватывают трафик всех profile-ов ниже,
    поэтому новый profile встаёт перед ними. Начало пресета (git.zapret.moe и
    соседние profile-ы) не трогается.
    """
    for index, profile in enumerate(tuple(preset.profiles or ())):
        if _is_ru_exclusion_profile(profile) or _is_catch_all_profile(profile):
            return index
    return len(preset.profiles)


def _is_ru_exclusion_profile(profile: Profile) -> bool:
    for line in (*profile.match.hostlist_lines, *profile.match.ipset_lines):
        _option, _separator, value = str(line or "").partition("=")
        for part in value.split(","):
            if _list_file_name(part) in _SERVICE_EXCLUDE_LIST_NAMES:
                return True
    return False


def _is_catch_all_profile(profile: Profile) -> bool:
    match = profile.match
    return not (
        match.hostlist_lines
        or match.hostlist_domains_lines
        or match.ipset_lines
        or match.inline_ipset_lines
    )


def _list_file_name(value: str) -> str:
    return PureWindowsPath(str(value or "").strip().strip('"').strip("'").lstrip("@")).name.lower()


def _is_service_exclusion_profile(profile: Profile) -> bool:
    if not (profile.match.hostlist_exclude_lines or profile.match.ipset_exclude_lines):
        return False
    if profile.match.hostlist_lines or profile.match.ipset_lines:
        return False

    text_parts = [
        str(getattr(profile, "name", "") or ""),
        str(getattr(profile, "display_name", "") or ""),
    ]
    if "исключ" in " ".join(text_parts).casefold():
        return True

    for line in (*profile.match.hostlist_exclude_lines, *profile.match.ipset_exclude_lines):
        _option, _separator, value = str(line or "").partition("=")
        for part in value.split(","):
            if _list_file_name(part) in _SERVICE_EXCLUDE_LIST_NAMES:
                return True
    return False


def _first_strategy_line_index(segments) -> int:
    """Индекс первой ``--lua-desync`` (len(segments) — у profile нет стратегии)."""
    items = tuple(segments or ())
    return next((index for index, segment in enumerate(items) if segment.kind == "strategy"), len(items))


def _effective_range(segments, before_index: int, option_name: str) -> str:
    value = _DEFAULT_RANGE_BY_OPTION[option_name]
    for segment in tuple(segments or ())[:before_index]:
        if segment.kind == "strategy_filter" and str(segment.name or "").strip().lower() == option_name:
            value = str(segment.value or "").strip() or _DEFAULT_RANGE_BY_OPTION[option_name]
    return value


def _last_range_segment_index(segments, first: int, stop: int, option_name: str) -> int | None:
    found: int | None = None
    for index in range(max(0, first), min(stop, len(segments))):
        segment = segments[index]
        if segment.kind == "strategy_filter" and str(segment.name or "").strip().lower() == option_name:
            found = index
    return found


def _range_segment(option_name: str, expression: str) -> ProfileSegment:
    return ProfileSegment(
        kind="strategy_filter",
        text=f"{option_name}={expression}",
        name=option_name,
        value=expression,
    )


def _with_profile_range(
    segments: list[ProfileSegment],
    option_name: str,
    value: str,
) -> list[ProfileSegment]:
    requested = _canonical_range_expression(option_name, value)
    start = _first_strategy_line_index(segments)
    previous = _effective_range(segments, start, option_name)
    if _same_range(option_name, previous, requested):
        return segments

    result = list(segments)
    own_index = _last_range_segment_index(result, 0, start, option_name)
    if own_index is not None:
        result[own_index] = _range_segment(option_name, requested)
    else:
        result.insert(_range_insert_index(result, start), _range_segment(option_name, requested))
    return result


def _range_insert_index(segments: list[ProfileSegment], first_strategy_index: int) -> int:
    if first_strategy_index < len(segments):
        return first_strategy_index
    insert_at = len(segments)
    while insert_at > 0 and segments[insert_at - 1].kind == "blank":
        insert_at -= 1
    return insert_at


def _same_range(option_name: str, left: str, right: str) -> bool:
    try:
        return _canonical_range_expression(option_name, left) == _canonical_range_expression(option_name, right)
    except ValueError:
        return False


def _match_group_names(filter_kind: str, filter_role: str) -> frozenset[str]:
    kind = str(filter_kind or "").strip().lower()
    if str(filter_role or "").strip().lower() == "exclude":
        if kind == "ipset":
            return frozenset({"--ipset-exclude", "--ipset-exclude-ip"})
        return frozenset({"--hostlist-exclude", "--hostlist-exclude-domains"})
    return frozenset({f"--{kind}"})


def _replace_match_group(
    segments: list[ProfileSegment],
    group_names: frozenset[str],
    filter_lines: list[str],
) -> list[ProfileSegment]:
    """Заменяет строки одной группы match-фильтра, остальные строки profile не трогает."""
    replacement = [
        ProfileSegment(kind="match", text=line, name=name, value=value)
        for line in filter_lines
        for name, value in (_split_option(line),)
    ]
    result: list[ProfileSegment] = []
    insert_at: int | None = None
    strategy_insert_at: int | None = None
    for segment in segments:
        name = str(segment.name or "").strip().lower()
        if segment.kind == "match" and name in group_names:
            if insert_at is None:
                insert_at = len(result)
            continue
        if strategy_insert_at is None and segment.kind in {"strategy_filter", "strategy"}:
            strategy_insert_at = len(result)
        result.append(segment)

    if insert_at is None:
        insert_at = strategy_insert_at if strategy_insert_at is not None else len(result)
    result[insert_at:insert_at] = replacement
    return result


def _canonical_range_expression(option_name: str, value: str) -> str:
    parsed = parse_out_range_expression(value, raw_line=f"{option_name}={value}")
    if parsed is None:
        raise ValueError(f"Invalid {ENGINE_WINWS2} packet range value: {value}")
    return parsed.expression


def normalize_filter_value(value: str, filter_kind: str, *, filter_role: str = "primary") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "," in raw:
        return ",".join(
            part
            for part in (_normalize_single_filter_value_for_kind(item, filter_kind) for item in raw.split(","))
            if part
        )
    return _normalize_single_filter_value_for_kind(raw, filter_kind)


def filter_value_is_file_reference(value: str) -> bool:
    parts = _split_filter_values(str(value or ""))
    return bool(parts) and all(_single_filter_value_is_file_reference(part) for part in parts)


def _single_filter_value_is_file_reference(value: str) -> bool:
    raw = str(value or "").strip().strip('"').strip("'").lstrip("@")
    if not raw or "://" in raw:
        return False
    file_name = PureWindowsPath(raw.replace("\\", "/")).name.strip()
    if not file_name or file_name in {".", ".."}:
        return False
    return file_name.lower().endswith(_LIST_FILE_EXTENSIONS)


def _filter_option_name(filter_kind: str, filter_role: str) -> str:
    if filter_role == "exclude":
        return f"{filter_kind}-exclude"
    return filter_kind


def _split_filter_values(filter_value: str) -> list[str]:
    return [part.strip() for part in str(filter_value or "").split(",") if part.strip()]


def _normalize_single_filter_value_for_kind(value: str, filter_kind: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    return raw


def _split_option(line: str) -> tuple[str, str]:
    if "=" not in line:
        return line, ""
    name, _, value = line.partition("=")
    return name.strip(), value.strip()


def _reparse(preset: Preset) -> Preset:
    return parse_preset_text(serialize_preset(preset), engine=preset.engine, source_name=preset.source_name)
