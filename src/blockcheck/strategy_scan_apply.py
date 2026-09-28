from __future__ import annotations

from pathlib import Path, PureWindowsPath

from blockcheck.strategy_scan_state import StrategyApplyResult
from blockcheck.strategy_scan_targeting import (
    default_target_for_protocol,
    normalize_target_domain,
)
from config.runtime_layout import APPLICATION_PATHS


def _line_option_values(line: str) -> list[str]:
    _name, separator, value = str(line or "").strip().partition("=")
    if not separator:
        return []
    return [part.strip().strip('"').strip("'") for part in value.split(",") if part.strip()]


def _domain_matches_hostlist_entry(target: str, entry: str) -> bool:
    target = normalize_target_domain(target) or ""
    entry = normalize_target_domain(str(entry or "").strip().lstrip(".")) or ""
    if entry.startswith("*."):
        entry = entry[2:]
    if not target or not entry:
        return False
    return target == entry or target.endswith(f".{entry}")


def _hostlist_line_domain(line: str) -> str:
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("#"):
        return ""
    stripped = stripped.split("#", 1)[0].strip()
    if not stripped:
        return ""
    return stripped.split()[0].strip()


def _hostlist_path_candidates(raw_path: str) -> list[Path]:
    clean = str(raw_path or "").strip().strip('"').strip("'").lstrip("@")
    if not clean:
        return []

    normalized = clean.replace("\\", "/")
    raw = Path(normalized)
    base = APPLICATION_PATHS.root
    file_name = PureWindowsPath(normalized).name

    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    candidates.append(base / normalized)
    if file_name:
        candidates.append(base / "lists" / file_name)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _hostlist_contains_target(raw_path: str, target: str) -> bool:
    for candidate in _hostlist_path_candidates(raw_path):
        try:
            with candidate.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                for line in handle:
                    if _domain_matches_hostlist_entry(target, _hostlist_line_domain(line)):
                        return True
        except OSError:
            continue
    return False


def _ports_include(ports: str, wanted_port: int) -> bool:
    for raw_part in str(ports or "").split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, _separator, end_text = part.partition("-")
            try:
                start = int(start_text.strip())
                end = int(end_text.strip())
            except ValueError:
                continue
            if start <= wanted_port <= end:
                return True
            continue
        try:
            if int(part) == wanted_port:
                return True
        except ValueError:
            continue
    return False


def _profile_accepts_tcp_https(profile) -> bool:
    for line in getattr(profile.match, "filter_lines", []) or []:
        stripped = str(line or "").strip().lower()
        if not stripped.startswith("--filter-tcp="):
            continue
        if _ports_include(stripped.partition("=")[2], 443):
            return True
    return False


def _profile_host_match_contains_target(profile, target: str) -> bool:
    for line in getattr(profile.match, "hostlist_domains_lines", []) or []:
        if any(_domain_matches_hostlist_entry(target, value) for value in _line_option_values(line)):
            return True

    for line in getattr(profile.match, "hostlist_lines", []) or []:
        if any(_hostlist_contains_target(value, target) for value in _line_option_values(line)):
            return True

    return False


def _find_existing_tcp_https_profile_for_target(profiles, target: str):
    normalized_target = normalize_target_domain(target) or ""
    if not normalized_target:
        return None
    for profile in profiles:
        if not _profile_accepts_tcp_https(profile):
            continue
        if _profile_host_match_contains_target(profile, normalized_target):
            return profile
    return None


_WINDIVERT_PORT_FILTER_OPTIONS = ("--wf-tcp-out", "--wf-tcp-in", "--wf-udp-out", "--wf-udp-in")
_SCANNED_PAYLOADS = {
    "tcp_https": ("tls_client_hello",),
    "stun_voice": ("stun", "discord_ip_discovery"),
}


def _parse_port_range(token: str) -> tuple[int, int] | None:
    start_text, separator, end_text = str(token or "").strip().partition("-")
    try:
        start = int(start_text)
        end = int(end_text) if separator else start
    except ValueError:
        return None
    return (start, end) if start <= end else None


def _ports_cover(ranges: list[tuple[int, int]], wanted: tuple[int, int]) -> bool:
    position = wanted[0]
    for start, end in sorted(ranges):
        if start > position:
            break
        if end >= position:
            position = end + 1
            if position > wanted[1]:
                return True
    return position > wanted[1]


def merge_windivert_port_filter(existing: str, extra: str) -> str:
    """Объединение двух значений `--wf-*`: результат ловит порты обоих.

    winws2 собирает порты одного параметра через OR, поэтому объединение —
    это дописывание новых элементов в конец. Элемент, уже покрытый обычными
    (без `~`) портами/диапазонами, не дописывается; порядок и вид исходных
    элементов не меняются.
    """
    tokens = [part.strip() for part in str(existing or "").split(",") if part.strip()]
    positive = [rng for rng in (_parse_port_range(token) for token in tokens if not token.startswith("~")) if rng]
    for token in (part.strip() for part in str(extra or "").split(",")):
        if not token or token in tokens:
            continue
        rng = None if token.startswith("~") else _parse_port_range(token)
        if rng is not None and _ports_cover(positive, rng):
            continue
        tokens.append(token)
        if rng is not None:
            positive.append(rng)
    return ",".join(tokens)


def _merge_preamble_lines(preamble_lines: list[str], extra_lines: list[str]) -> None:
    """Добавляет в преамбулу строки найденной стратегии, не ломая перехват пресета.

    Для `--wf-tcp/udp-in/out` в winws2 действует ПОСЛЕДНЯЯ строка параметра:
    вторая строка заменила бы весь перехват пресета (например, порты голоса).
    Поэтому порты объединяются в последнюю существующую строку, а новая строка
    появляется, только если такого параметра в пресете ещё нет.
    """
    known = {line.strip() for line in preamble_lines if line.strip()}
    for raw in extra_lines:
        line = str(raw or "").strip()
        if not line or line in known:
            continue
        option, separator, value = line.partition("=")
        option = option.strip().lower()
        if separator and option in _WINDIVERT_PORT_FILTER_OPTIONS:
            existing_index = next(
                (
                    index
                    for index in range(len(preamble_lines) - 1, -1, -1)
                    if preamble_lines[index].strip().lower().startswith(f"{option}=")
                ),
                None,
            )
            if existing_index is not None:
                current = preamble_lines[existing_index].strip()
                current_option, _separator, current_value = current.partition("=")
                merged = merge_windivert_port_filter(current_value, value)
                if merged != current_value.strip():
                    preamble_lines[existing_index] = f"{current_option}={merged}"
                known.add(preamble_lines[existing_index].strip())
                continue
        if preamble_lines and preamble_lines[-1].strip():
            preamble_lines.append("")
        preamble_lines.append(line)
        known.add(line)


def _payload_covers(payload: str, scanned: tuple[str, ...]) -> bool:
    tokens = {part.strip().lower() for part in str(payload or "all").split(",") if part.strip()}
    if not scanned or tokens & {"all", "known"}:
        return True
    return set(scanned) <= tokens


def _lua_lines(profile) -> list[str]:
    return [
        str(segment.text or "").strip()
        for segment in profile.segments
        if segment.kind == "strategy" and str(segment.text or "").strip().lower().startswith("--lua-desync=")
    ]


def _expect_profile_lua_lines(profile_index: int, lua_lines: list[str]):
    """Проверка после записи: profile включён и содержит строки найденной стратегии подряд."""
    wanted = [line.strip() for line in lua_lines if line.strip()]

    def _expect(stored) -> str:
        profiles = tuple(getattr(stored, "profiles", ()) or ())
        if not (0 <= profile_index < len(profiles)):
            return f"profile_missing_after_write: index={profile_index}"
        profile = profiles[profile_index]
        if not profile.enabled:
            return "profile_disabled_after_write"
        actual = _lua_lines(profile)
        for start in range(0, len(actual) - len(wanted) + 1):
            if actual[start : start + len(wanted)] == wanted:
                return ""
        return f"strategy_missing_after_write: expected={wanted} actual={actual}"

    return _expect


def _with_strategy_in_existing_profile(source, profile, new_profile, ready_strategy_lines, scan_protocol: str):
    """Ставит найденную стратегию в существующий profile.

    Пользователь явно применяет найденную стратегию к этому profile-у, поэтому
    profile включается. Правило то же, что у выбора готовой стратегии на
    странице profile-а (``profile.serializer.with_profile_ready_strategy``):
    у profile-а одна стратегия, диапазоны profile-а до первой ``--lua-desync``
    остаются, а поверх составной стратегии пишется один ``--payload`` с
    объединением типов пакетов прежних веток. Если прежний ``--payload``
    profile-а не пропускает проверенный трафик (TLS ClientHello для HTTPS,
    STUN для голоса), его типы добавляются к объединению — иначе найденная
    стратегия не сработала бы на том, на чём её проверяли.
    Profile без стратегии получает строки найденного profile-а целиком.
    """
    from profile.serializer import (
        with_profile_enabled,
        with_profile_ready_strategy,
        with_profile_strategy_lines,
        with_profile_whole_strategy,
    )
    from profile.strategy_shape import PAYLOAD_OPTION, is_lua_desync_line, strategy_shape, union_payload

    ready_lines = [str(line or "").strip() for line in ready_strategy_lines or () if str(line or "").strip()]
    ready_lua_lines = [line for line in ready_lines if is_lua_desync_line(line)]
    index = profile.index
    current = strategy_shape(getattr(profile.strategy, "strategy_lines", ()) or ())
    scanned = _SCANNED_PAYLOADS.get(str(scan_protocol or "").strip(), ())
    if not current.lua_lines:
        updated = with_profile_strategy_lines(
            source,
            index,
            list(getattr(new_profile.strategy, "strategy_lines", ()) or ()),
        )
    elif strategy_shape(ready_lines).composite or _payload_covers(union_payload(current.payload_scopes), scanned):
        updated, _whole = with_profile_ready_strategy(source, index, ready_lines)
    else:
        payload = union_payload((*current.payload_scopes, ",".join(scanned)))
        updated = with_profile_whole_strategy(source, index, (f"{PAYLOAD_OPTION}={payload}", *ready_lines))
    return with_profile_enabled(updated, index, True), _expect_profile_lua_lines(index, ready_lua_lines)


def plan_selected_preset_strategy_apply(
    source,
    *,
    strategy_lines: list[str],
    ready_strategy_lines: list[str],
    match_target: str = "",
    scan_protocol: str = "",
):
    """Чистый план правки выбранного пресета: (новый пресет, "created"/"updated", expect).

    ``strategy_lines`` — profile найденной стратегии целиком (для нового
    profile-а), ``ready_strategy_lines`` — сама найденная готовая стратегия
    (для существующего profile-а).
    """
    from copy import deepcopy

    from profile.parser import parse_preset_text
    from profile.serializer import append_profile_from_template
    from settings.mode import ENGINE_WINWS2

    updated = deepcopy(source)
    cleaned_strategy_lines = [line.strip() for line in strategy_lines if line and line.strip()]
    profile_source = "\n".join(cleaned_strategy_lines).rstrip("\n") + "\n"
    profile_preset = parse_preset_text(profile_source, engine=ENGINE_WINWS2, source_name=updated.source_name)
    if not profile_preset.profiles:
        raise RuntimeError("Не удалось собрать profile из найденной стратегии")

    _merge_preamble_lines(updated.preamble_lines, profile_preset.preamble_lines)

    new_profile = profile_preset.profiles[0]
    existing_profile = next(
        (
            profile
            for profile in updated.profiles
            if profile.match_signature and profile.match_signature == new_profile.match_signature
        ),
        None,
    )
    if existing_profile is None and str(scan_protocol or "").strip() == "tcp_https":
        existing_profile = _find_existing_tcp_https_profile_for_target(updated.profiles, match_target)
    if existing_profile is not None:
        planned, expect = _with_strategy_in_existing_profile(
            updated,
            existing_profile,
            new_profile,
            ready_strategy_lines,
            scan_protocol,
        )
        return planned, "updated", expect

    # Найденная стратегия проверена на конкретной цели — её profile идёт
    # первым, чтобы более широкие profile-ы пресета не перехватили цель.
    planned = append_profile_from_template(updated, new_profile, enabled=True, position="top")
    return planned, "created", _expect_profile_lua_lines(0, _lua_lines(new_profile))


def apply_profile_to_selected_preset(
    *,
    profile_feature,
    strategy_lines: list[str],
    ready_strategy_lines: list[str],
    match_target: str = "",
    scan_protocol: str = "",
) -> tuple[str, str, tuple[str, ...]]:
    """Пишет найденную стратегию в выбранный пресет.

    Пользователь явно применяет найденную стратегию, поэтому в преамбулу
    пресета дописываются объявления ``--blob=`` для её фейков, которых в
    пресете ещё нет (``profile.preset_blob_declarations``). Возвращает
    (файл пресета, "created"/"updated", предупреждения про фейки).
    """
    from log.log import log
    from profile.preset_blob_declarations import with_declared_blobs
    from settings.mode import ZAPRET2_MODE

    load_catalog = getattr(profile_feature, "load_fakes_catalog", None)

    def _edit(source):
        planned, operation, expect = plan_selected_preset_strategy_apply(
            source,
            strategy_lines=strategy_lines,
            ready_strategy_lines=ready_strategy_lines,
            match_target=match_target,
            scan_protocol=scan_protocol,
        )
        planned, report = with_declared_blobs(planned, strategy_lines, load_catalog)
        return planned, (operation, report), expect

    selected_file_name, (operation, report) = profile_feature.edit_selected_preset(ZAPRET2_MODE, _edit)
    if not selected_file_name:
        raise RuntimeError("Не удалось определить выбранный пресет")
    if report.added:
        log(f"Blockcheck: в пресет добавлены фейки стратегии: {', '.join(report.added)}", "INFO")
    if report.conflicts:
        log(
            "Blockcheck: пресет объявляет фейки иначе, чем реестр (оставлено как в пресете): "
            + ", ".join(report.conflicts),
            "INFO",
        )
    warnings = report.user_warnings()
    for warning in warnings:
        log(f"Blockcheck: {warning}", "WARNING")
    return selected_file_name, operation, warnings


def apply_strategy(
    *,
    profile_feature,
    strategy_args: str,
    strategy_name: str,
    scan_target: str,
    scan_protocol: str,
    apply_lines,
) -> StrategyApplyResult:
    """Записать в выбранный пресет найденную стратегию.

    ``apply_lines`` — ровно те строки, на которых подбор проверял стратегию
    (``strategy_search.probe_profile``). Здесь они не пересобираются: что
    проверили, то и записали.
    """
    new_strategy_lines = [str(line).strip() for line in (apply_lines or ()) if str(line or "").strip()]
    if not new_strategy_lines:
        raise RuntimeError("У стратегии нет проверенного профиля — запустите подбор ещё раз")
    target = scan_target or default_target_for_protocol(scan_protocol)

    if scan_protocol == "stun_voice":
        applied_profile = f"голосовые звонки, проверка {target}"
    elif scan_protocol == "udp_games":
        applied_profile = f"игры UDP, проверка {target}"
    else:
        applied_profile = normalize_target_domain(target) or target

    selected_file_name, operation, blob_warnings = apply_profile_to_selected_preset(
        profile_feature=profile_feature,
        strategy_lines=new_strategy_lines,
        ready_strategy_lines=str(strategy_args or "").splitlines(),
        match_target=target,
        scan_protocol=scan_protocol,
    )

    return StrategyApplyResult(
        strategy_name=strategy_name,
        applied_profile=applied_profile,
        selected_file_name=selected_file_name,
        operation=operation,
        blob_warnings=blob_warnings,
    )
