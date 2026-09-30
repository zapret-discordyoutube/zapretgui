from __future__ import annotations

from copy import deepcopy
import re

from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2

from .models import EngineName, Preset, Profile, ProfileSegment
from .parser import _name_from_new_line, parse_preset_text
from .strategy_shape import PAYLOAD_OPTION, RANGE_OPTIONS, strategy_shape, union_payload


_STRATEGY_KINDS = {"strategy", "strategy_filter"}
_LIST_FILE_MATCH_NAMES = {
    "--hostlist",
    "--ipset",
    "--hostlist-exclude",
    "--ipset-exclude",
    "--hostlist-auto",
}


def serialize_preset(preset: Preset) -> str:
    lines: list[str] = []
    lines.extend(preset.header_lines)
    lines.extend(preset.preamble_lines)

    for profile in preset.profiles:
        skip_leading_blanks = False
        if profile.new_line:
            while lines and not lines[-1].strip():
                lines.pop()
            if lines:
                lines.append("")
            lines.append(profile.new_line)
            lines.append("")
            skip_leading_blanks = True
        for segment in profile.segments:
            if segment.kind == "blank":
                continue
            skip_leading_blanks = False
            lines.append(segment.text)
    lines.extend(getattr(preset, "footer_lines", []) or [])

    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def with_profile_enabled(preset: Preset, profile_index: int, enabled: bool) -> Preset:
    updated = deepcopy(preset)
    profile = updated.profiles[int(profile_index)]
    profile.enabled = bool(enabled)
    has_skip = any(segment.kind == "directive" and segment.text.strip().lower() == "--skip" for segment in profile.segments)
    if profile.enabled:
        profile.segments = [
            segment
            for segment in profile.segments
            if not (segment.kind == "directive" and segment.text.strip().lower() == "--skip")
        ]
        return _reparse(updated)

    if not has_skip:
        insert_at = _directive_insert_index(profile)
        profile.segments.insert(insert_at, ProfileSegment(kind="directive", text="--skip", name="--skip"))
    return _reparse(updated)


def with_profile_strategy_lines(preset: Preset, profile_index: int, strategy_lines: list[str]) -> Preset:
    updated = deepcopy(preset)
    profile = updated.profiles[int(profile_index)]
    normalized_lines = [str(line or "").strip() for line in strategy_lines or [] if str(line or "").strip()]
    normalized_lines = _preserve_missing_winws2_strategy_filters(updated.engine, profile, normalized_lines)
    replacement_segments = [
        _segment_for_strategy_line(updated.engine, line)
        for line in normalized_lines
    ]

    first_strategy_index = None
    kept: list[ProfileSegment] = []
    for index, segment in enumerate(profile.segments):
        if segment.kind in _STRATEGY_KINDS:
            if first_strategy_index is None:
                first_strategy_index = len(kept)
            continue
        kept.append(segment)

    insert_at = first_strategy_index if first_strategy_index is not None else len(kept)
    if replacement_segments:
        while insert_at > 0 and kept[insert_at - 1].kind == "blank":
            del kept[insert_at - 1]
            insert_at -= 1
    profile.segments = [*kept[:insert_at], *replacement_segments, *kept[insert_at:]]
    return _reparse(updated)


def with_profile_whole_strategy(preset: Preset, profile_index: int, strategy_lines) -> Preset:
    """Пресет, где стратегия profile-а (winws2) заменена строками целиком.

    Остаются только настройки profile-а — строки ``--in-range``/``--out-range``
    до первой ``--lua-desync`` (см. ``profile.strategy_shape``); все остальные
    строки стратегии (``--payload``, диапазоны между ветками, ``--lua-desync``)
    удаляются, а новые строки вставляются как есть. Так применяется составная
    готовая стратегия и обычная стратегия поверх составной.
    """
    updated = deepcopy(preset)
    profile = updated.profiles[int(profile_index)]
    replacement = [
        _segment_for_strategy_line(updated.engine, line)
        for line in (str(raw or "").strip() for raw in strategy_lines or ())
        if line
    ]
    first_lua = next(
        (index for index, segment in enumerate(profile.segments) if segment.kind == "strategy"),
        None,
    )
    kept: list[ProfileSegment] = []
    first_removed: int | None = None
    after_last_profile_range: int | None = None
    for index, segment in enumerate(profile.segments):
        if segment.kind in _STRATEGY_KINDS:
            is_profile_range = (
                segment.kind == "strategy_filter"
                and str(segment.name or "").strip().lower() in RANGE_OPTIONS
                and (first_lua is None or index < first_lua)
            )
            if not is_profile_range:
                if first_removed is None:
                    first_removed = len(kept)
                continue
            kept.append(segment)
            after_last_profile_range = len(kept)
            continue
        kept.append(segment)

    if first_removed is None:
        insert_at = len(kept)
        while insert_at > 0 and kept[insert_at - 1].kind == "blank":
            insert_at -= 1
    else:
        insert_at = first_removed
    # Диапазоны profile-а должны остаться ДО первой --lua-desync новой стратегии.
    if after_last_profile_range is not None:
        insert_at = max(insert_at, after_last_profile_range)
    profile.segments = [*kept[:insert_at], *replacement, *kept[insert_at:]]
    return _reparse(updated)


def with_profile_ready_strategy(
    preset: Preset,
    profile_index: int,
    strategy_lines,
) -> tuple[Preset, tuple[str, ...] | None]:
    """Пресет с выбранной готовой стратегией у profile-а — единое правило для
    выбора на странице profile-а и для «Применить» в blockcheck.

    winws2 (см. ``profile.strategy_shape``):

    - составная стратегия: её строки целиком вместо стратегии profile-а
      (остаются только диапазоны profile-а до первой ``--lua-desync``);
    - обычная стратегия поверх составной: один ``--payload`` с объединением
      типов пакетов прежних веток (``all``, если хоть одна ветка была на все
      пакеты) и строки стратегии;
    - обычная стратегия поверх обычной: меняются только строки
      ``--lua-desync``, ``--payload`` и диапазоны profile-а остаются.

    Второй элемент — строки стратегии, записанные целиком (для проверки после
    записи), или None, если заменены только строки ``--lua-desync``.
    """
    lines = [str(line or "").strip() for line in strategy_lines or () if str(line or "").strip()]
    if preset.engine == ENGINE_WINWS2:
        profile = preset.profiles[int(profile_index)]
        current = strategy_shape(getattr(profile.strategy, "strategy_lines", ()) or ())
        if strategy_shape(lines).composite:
            whole: tuple[str, ...] | None = tuple(lines)
        elif current.composite:
            whole = (f"{PAYLOAD_OPTION}={union_payload(current.payload_scopes)}", *lines)
        else:
            whole = None
        if whole is not None:
            return with_profile_whole_strategy(preset, profile_index, whole), whole
    return with_profile_strategy_lines(preset, profile_index, lines), None


def with_profile_user_match(
    preset: Preset,
    profile_index: int,
    *,
    name: str,
    protocol: str,
    ports: str,
    hostlist: str,
    ipset: str,
) -> Preset:
    updated = deepcopy(preset)
    profile = updated.profiles[int(profile_index)]
    clean_name = str(name or "").strip()
    clean_protocol = str(protocol or "").strip().lower()
    clean_ports = str(ports or "").strip()
    hostlist = str(hostlist or "").strip()
    ipset = str(ipset or "").strip()
    if clean_protocol not in {"tcp", "udp", "l7"}:
        raise ValueError("protocol must be tcp, udp or l7")
    if not clean_name or not clean_ports:
        raise ValueError("name and ports are required")

    filter_line = f"--filter-{clean_protocol}={clean_ports}"
    name_written = False
    filter_written = False
    list_written = False
    insert_at: int | None = None
    result: list[ProfileSegment] = []

    for segment in profile.segments:
        segment_name = str(segment.name or "").strip().lower()
        name_directive = _profile_name_directive_name(updated.engine)
        if segment.kind == "directive" and segment_name == name_directive:
            result.append(_profile_name_segment(updated.engine, clean_name))
            name_written = True
            continue
        if segment.kind == "match" and segment_name.startswith("--filter-"):
            if not filter_written:
                result.append(_segment_for_match_line(filter_line))
                filter_written = True
            continue
        if segment.kind == "match" and segment_name in {"--hostlist", "--ipset"}:
            if segment_name == "--ipset":
                if ipset:
                    result.append(_segment_for_match_line(f"--ipset={ipset}"))
                    list_written = True
                continue
            if hostlist:
                result.append(_segment_for_match_line(f"--hostlist={hostlist}"))
                list_written = True
            continue
        if insert_at is None and segment.kind in {"match", "strategy_filter", "strategy"}:
            insert_at = len(result)
        result.append(segment)

    if not name_written:
        result.insert(_directive_insert_index_for_segments(result), _profile_name_segment(updated.engine, clean_name))
    if not filter_written:
        insert = _first_strategy_or_end_index(result)
        result.insert(insert, _segment_for_match_line(filter_line))
    if not list_written and hostlist:
        insert = _first_strategy_or_end_index(result)
        result.insert(insert, _segment_for_match_line(f"--hostlist={hostlist}"))

    profile.segments = result
    return _reparse(updated)


def _preserve_missing_winws2_strategy_filters(engine: EngineName, profile: Profile, strategy_lines: list[str]) -> list[str]:
    if engine != ENGINE_WINWS2:
        return strategy_lines

    wanted_names = ("--payload", "--in-range", "--out-range")
    provided = {
        _split_option(line)[0].strip().lower()
        for line in strategy_lines
    }
    # Фильтры, стоящие ДО первой строки стратегии, — они и задают, к чему
    # применяется начало стратегии. Из подряд идущих одноимённых действует
    # последний (как в winws2), поэтому берём последний, а не первый; фильтры
    # следующих веток (после первого --lua-desync) к новой стратегии не относятся.
    leading: dict[str, str] = {}
    for segment in profile.segments:
        if segment.kind == "strategy":
            break
        if segment.kind != "strategy_filter":
            continue
        name = str(segment.name or "").strip().lower()
        if name not in wanted_names or name in provided:
            continue
        text = str(segment.text or "").strip()
        if text:
            leading.pop(name, None)
            leading[name] = text
    return [*leading.values(), *strategy_lines]


def append_profile_from_template(
    preset: Preset,
    template: Profile,
    *,
    enabled: bool = True,
    position: str | int = "bottom",
) -> Preset:
    """Вставляет profile из шаблона: "top", "bottom" или индекс profile-а.

    Хвост пресета (`footer_lines`) принадлежит файлу и не удаляется.
    """
    updated = deepcopy(preset)
    insert_at = _template_insert_index(position, len(updated.profiles))
    if (
        updated.profiles
        and insert_at == len(updated.profiles)
        and updated.profiles[-1].segments
        and updated.profiles[-1].segments[-1].text.strip()
    ):
        updated.profiles[-1].segments.append(ProfileSegment(kind="blank", text=""))
    profile = deepcopy(template)
    profile.index = insert_at
    profile.engine = updated.engine
    profile.new_line = "" if insert_at == 0 else "--new"
    profile.segments = [
        segment
        for segment in profile.segments
        if not (segment.kind == "directive" and segment.text.strip().lower() == "--skip")
    ]
    _canonicalize_template_match_paths(profile)
    if not enabled:
        profile.segments.insert(_directive_insert_index(profile), ProfileSegment(kind="directive", text="--skip", name="--skip"))
    _ensure_safe_default_strategy(profile)
    updated.profiles.insert(insert_at, profile)
    _ensure_profile_boundaries(updated)
    return _reparse(updated)


def _template_insert_index(position: str | int, profile_count: int) -> int:
    if isinstance(position, int) and not isinstance(position, bool):
        return max(0, min(int(position), profile_count))
    clean = str(position or "").strip().lower()
    if clean == "top":
        return 0
    if clean == "bottom":
        return profile_count
    raise ValueError(f"Unsupported template profile position: {position}")


def with_profile_deleted(preset: Preset, profile_index: int) -> Preset:
    updated = deepcopy(preset)
    index = int(profile_index)
    if index < 0 or index >= len(updated.profiles):
        raise IndexError(f"Profile index out of range: {profile_index}")
    del updated.profiles[index]
    _ensure_profile_boundaries(updated)
    return _reparse(updated)


def with_profile_duplicated(preset: Preset, profile_index: int) -> Preset:
    updated = deepcopy(preset)
    index = int(profile_index)
    if index < 0 or index >= len(updated.profiles):
        raise IndexError(f"Profile index out of range: {profile_index}")

    source = updated.profiles[index]
    profile = deepcopy(source)
    profile.index = index + 1
    _rename_profile_copy(profile, _unique_copy_name(updated, source))

    updated.profiles.insert(index + 1, profile)
    _ensure_profile_boundaries(updated)
    return _reparse(updated)


def with_profile_moved(preset: Preset, source_index: int, destination_index: int) -> Preset:
    updated = deepcopy(preset)
    source = int(source_index)
    destination = int(destination_index)
    if source < 0 or source >= len(updated.profiles):
        raise IndexError(f"Profile index out of range: {source_index}")
    if destination < 0:
        destination = 0
    if destination > len(updated.profiles):
        destination = len(updated.profiles)
    if source == destination or source + 1 == destination:
        return updated

    profile = updated.profiles.pop(source)
    if source < destination:
        destination -= 1
    updated.profiles.insert(destination, profile)
    _ensure_profile_boundaries(updated)
    return _reparse(updated)


def with_profile_raw_text(preset: Preset, profile_index: int, raw_text: str) -> Preset:
    updated = deepcopy(preset)
    index = int(profile_index)
    if index < 0 or index >= len(updated.profiles):
        raise IndexError(f"Profile index out of range: {profile_index}")

    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("profile text must not be empty")

    # Текст разбирается как профиль после «--new», а не как отдельный пресет:
    # иначе ведущие «# комментарии» уходили в шапку разобранного пресета и
    # молча пропадали при сохранении.
    parsed = parse_preset_text(f"--new\n{text}", engine=updated.engine, source_name=updated.source_name)
    if len(parsed.profiles) != 1 or parsed.header_lines or parsed.preamble_lines:
        raise ValueError("profile text must contain exactly one profile")

    replacement = deepcopy(parsed.profiles[0])
    replacement.index = index
    replacement.engine = updated.engine
    # Граница профиля («--new» / «--new=Имя») не часть его текста — остаётся
    # прежней, вместе с именем, записанным в ней.
    current_new_line = str(updated.profiles[index].new_line or "")
    replacement.new_line = current_new_line
    if not str(replacement.name or "").strip():
        replacement.name = _name_from_new_line(current_new_line)
    updated.profiles[index] = replacement
    _ensure_profile_boundaries(updated)
    return _reparse(updated)


def _segment_for_strategy_line(engine: EngineName, line: str) -> ProfileSegment:
    lowered = line.lower()
    if engine == ENGINE_WINWS2 and (
        lowered.startswith("--payload=")
        or lowered.startswith("--in-range")
        or lowered.startswith("--out-range")
    ):
        name, value = _split_option(line)
        return ProfileSegment(kind="strategy_filter", text=line, name=name, value=value)
    name, value = _split_option(line)
    return ProfileSegment(kind="strategy", text=line, name=name, value=value)


def _segment_for_match_line(line: str) -> ProfileSegment:
    name, value = _split_option(line)
    return ProfileSegment(kind="match", text=line, name=name, value=value)


def _canonicalize_template_match_paths(profile: Profile) -> None:
    for segment in profile.segments:
        name = str(segment.name or "").strip().lower()
        if segment.kind != "match" or name not in _LIST_FILE_MATCH_NAMES:
            continue
        value = _lists_relative_value(str(segment.value or ""))
        if value == str(segment.value or ""):
            continue
        segment.value = value
        segment.text = f"{segment.name}={value}"


def _lists_relative_value(value: str) -> str:
    raw = str(value or "").strip()
    clean = raw.strip('"').strip("'")
    if not clean:
        return raw
    normalized = clean.replace("\\", "/")
    if "/" in normalized or ":" in normalized or normalized.startswith("@"):
        return raw
    return f"lists/{clean}"


def _directive_insert_index_for_segments(segments: list[ProfileSegment]) -> int:
    for index, segment in enumerate(segments):
        if segment.kind not in {"blank", "comment", "directive"}:
            return index
    return len(segments)


def _first_strategy_or_end_index(segments: list[ProfileSegment]) -> int:
    for index, segment in enumerate(segments):
        if segment.kind in {"strategy_filter", "strategy"}:
            return index
    return len(segments)


def _split_option(line: str) -> tuple[str, str]:
    if "=" not in line:
        return line, ""
    name, _, value = line.partition("=")
    return name.strip(), value.strip()


def _directive_insert_index(profile: Profile) -> int:
    for index, segment in enumerate(profile.segments):
        if segment.kind not in {"blank", "comment", "directive"}:
            return index
    return len(profile.segments)


def _unique_copy_name(preset: Preset, source: Profile) -> str:
    base = str(source.name or source.display_name or f"profile {source.index + 1}").strip() or "profile"
    base = re.sub(r"\s+копия(?:\s+\d+)?$", "", base, flags=re.IGNORECASE).strip() or base
    existing = {
        str(profile.name or profile.display_name or "").strip().casefold()
        for profile in preset.profiles
        if str(profile.name or profile.display_name or "").strip()
    }
    candidate = f"{base} копия"
    if candidate.casefold() not in existing:
        return candidate
    counter = 2
    while True:
        candidate = f"{base} копия {counter}"
        if candidate.casefold() not in existing:
            return candidate
        counter += 1


def _ensure_profile_boundaries(preset: Preset) -> None:
    for index, profile in enumerate(preset.profiles):
        if index == 0:
            _remove_leading_blank_segments(profile)
            _ensure_profile_name_directive(profile, preset.engine)
            profile.new_line = ""
            continue
        if _profile_has_name_directive(profile, preset.engine):
            profile.new_line = "--new"
            continue
        if preset.engine == ENGINE_WINWS1:
            # winws1 (nfqws1) объявляет --new без аргумента: `--new=имя` не запускается
            # ("option doesn't take an argument -- new"). Имя профиля в winws1 — только --comment.
            profile.new_line = "--new"
            continue
        # Только собственное имя профиля (из «--new=Имя»). Отображаемое имя
        # («TCP 443 • hostlist …») — вычисляемая подпись интерфейса: её запись
        # в файл незаметно меняла пресет, «замораживала» подпись и сдвигала
        # ключи безымянных профилей после перемещения/удаления соседей.
        name = str(profile.name or "").strip()
        profile.new_line = f"--new={name}" if name else "--new"


def _ensure_profile_name_directive(profile: Profile, engine: EngineName) -> None:
    if _profile_has_name_directive(profile, engine):
        return
    name = str(profile.name or "").strip()
    if not name:
        return
    profile.segments.insert(
        _directive_insert_index(profile),
        _profile_name_segment(engine, name),
    )


def _profile_has_name_directive(profile: Profile, engine: EngineName) -> bool:
    directive_name = _profile_name_directive_name(engine)
    return any(
        segment.kind == "directive" and str(segment.name or "").strip().lower() == directive_name
        for segment in profile.segments
    )


def _profile_name_directive_name(engine: EngineName) -> str:
    return "--comment" if engine == ENGINE_WINWS1 else "--name"


def _profile_name_segment(engine: EngineName, name: str) -> ProfileSegment:
    directive_name = _profile_name_directive_name(engine)
    return ProfileSegment(kind="directive", text=f"{directive_name}={name}", name=directive_name, value=name)


def _ensure_safe_default_strategy(profile: Profile) -> None:
    if profile.engine != ENGINE_WINWS2:
        return
    if any(segment.kind == "strategy" for segment in profile.segments):
        return
    insert_at = len(profile.segments)
    while insert_at > 0 and profile.segments[insert_at - 1].kind == "blank":
        insert_at -= 1
    profile.segments.insert(
        insert_at,
        ProfileSegment(kind="strategy", text="--lua-desync=pass", name="--lua-desync", value="pass"),
    )


def _remove_leading_blank_segments(profile: Profile) -> None:
    while profile.segments and profile.segments[0].kind == "blank":
        del profile.segments[0]


def _rename_profile_copy(profile: Profile, name: str) -> None:
    clean_name = str(name or "").strip() or "profile копия"
    renamed_new_line = False
    current_new_line = str(profile.new_line or "").strip()
    if current_new_line.lower().startswith("--new="):
        profile.new_line = f"--new={clean_name}"
        renamed_new_line = True

    for segment in profile.segments:
        if segment.kind == "directive" and str(segment.name or "").strip().lower() == _profile_name_directive_name(profile.engine):
            segment.value = clean_name
            segment.text = f"{segment.name}={clean_name}"
            profile.name = clean_name
            profile.display_name = clean_name
            return

    if renamed_new_line:
        profile.name = clean_name
        profile.display_name = clean_name
        return
    profile.new_line = f"--new={clean_name}"
    if current_new_line.lower() not in {"--new"}:
        profile.segments.insert(
            _directive_insert_index(profile),
            _profile_name_segment(profile.engine, clean_name),
        )
    profile.name = clean_name
    profile.display_name = clean_name


def _reparse(preset: Preset) -> Preset:
    return parse_preset_text(serialize_preset(preset), engine=preset.engine, source_name=preset.source_name)
