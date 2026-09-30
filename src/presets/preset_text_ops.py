from __future__ import annotations

import re

from presets.preset_contract import DEBUG_LOG_DIR, relocate_legacy_debug_log_file, strip_utf8_bom
from settings.mode import ENGINE_WINWS2


def _split_preset_lines(text: str) -> list[str]:
    """Строки пресета так же, как их видит парсер: только по «\\n».

    str.splitlines() режет ещё и по \\x0b, \\x0c, \\x85, \\u2028 — строка с
    таким символом внутри значения выглядела бы для этих функций иначе, чем
    для парсера и запуска. Хвостовой перевод строки не даёт пустой строки."""
    lines = str(text or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _rewrite_preset_headers(
    source_text: str,
    preset_name: str,
    *,
    preset_kind: str | None = None,
) -> str:
    text = strip_utf8_bom(source_text).replace("\r\n", "\n").replace("\r", "\n")
    lines = _split_preset_lines(text)

    header_end = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            header_end = idx
            break
    else:
        header_end = len(lines)

    header = lines[:header_end]
    body = lines[header_end:]
    out_header: list[str] = []
    saw_preset = False
    saw_preset_kind = False

    for raw in header:
        stripped = raw.strip()
        lowered = stripped.lower()
        if lowered.startswith("# preset:"):
            out_header.append(f"# Preset: {preset_name}")
            saw_preset = True
            continue
        if lowered.startswith("# presetkind:"):
            if preset_kind is not None:
                out_header.append(f"# PresetKind: {preset_kind}")
                saw_preset_kind = True
            else:
                out_header.append(raw.rstrip("\n"))
                saw_preset_kind = True
            continue
        if lowered.startswith("# modified:"):
            continue
        if lowered.startswith("# activepreset:"):
            continue
        out_header.append(raw.rstrip("\n"))

    if not saw_preset:
        out_header.insert(0, f"# Preset: {preset_name}")

    insert_idx = 1 if out_header and out_header[0].startswith("# Preset:") else 0
    if preset_kind is not None and not saw_preset_kind:
        out_header.insert(insert_idx, f"# PresetKind: {preset_kind}")

    rewritten = "\n".join(out_header + body).rstrip("\n")
    return rewritten + "\n"


def _header_preset_kind(kind: str | None) -> str | None:
    normalized = str(kind or "").strip().lower()
    if normalized == "imported":
        return "imported"
    return None


def validate_preset_source_text(source_text: str, *, engine: str = "") -> str:
    """Минимальная структурная проверка импортируемого пресета.

    Возвращает пустую строку для валидного текста или человекочитаемую
    причину отказа. Пресет winws состоит из строк-опций «--…», комментариев
    «#» и пустых строк; любой другой текст (лог, JSON, случайный файл) не
    должен превращаться в «пресет» при импорте.

    Рабочему пресету нужны и содержательные опции: без фильтра «--wf…»
    winws не перехватывает трафик, а пресет winws2 без единого
    «--lua-desync» ничего не делает с перехваченным. Для zapret1 опции
    --lua-desync не существует, поэтому там требуется только фильтр.
    """
    text = (source_text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    option_lines = 0
    has_wf_filter = False
    has_lua_desync = False
    for line_no, raw in enumerate(_split_preset_lines(text), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("--"):
            preview = stripped if len(stripped) <= 60 else f"{stripped[:57]}…"
            return f"строка {line_no} не является опцией winws: «{preview}»"
        option_lines += 1
        lowered = stripped.lower()
        if lowered.startswith("--wf"):
            has_wf_filter = True
        if lowered.startswith("--lua-desync"):
            has_lua_desync = True
    if not option_lines:
        return "в файле нет ни одной опции winws (строк вида «--…»)"
    if not has_wf_filter:
        return "нет ни одной опции фильтра «--wf…» — такой пресет не перехватывает трафик"
    if str(engine or "").strip().lower() == ENGINE_WINWS2 and not has_lua_desync:
        return "нет ни одной опции «--lua-desync» — такой пресет ничего не делает с трафиком"
    return ""


def _build_stable_debug_log_file(preset_name: str) -> str:
    safe_name = re.sub(r"[^\w.-]+", "_", str(preset_name or "").strip(), flags=re.UNICODE).strip("._")
    if not safe_name:
        safe_name = "preset"
    # Путь относителен корня установки (cwd запуска winws2), где логи
    # живут в user\logs.
    return f"{DEBUG_LOG_DIR}/{safe_name}_debug.log"


def _default_debug_insert_index(lines: list[str]) -> int:
    insert_at = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("--lua-init="):
            insert_at = idx + 1
    if insert_at:
        return insert_at

    header_end = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("#") or not stripped:
            header_end = idx + 1
            continue
        break
    return header_end


def _rewrite_debug_log_setting(source_text: str, preset_name: str, enabled: bool) -> str:
    text = (source_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = _split_preset_lines(text)

    existing_value = ""
    existing_insert_at: int | None = None
    cleaned: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.lower().startswith("--debug="):
            if not existing_value:
                existing_value = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
                existing_value = existing_value.lstrip("@").replace("\\", "/").lstrip("/")
                existing_insert_at = len(cleaned)
            continue
        cleaned.append(raw)

    if enabled:
        debug_file = relocate_legacy_debug_log_file(existing_value) or _build_stable_debug_log_file(preset_name)
        debug_line = f"--debug=@{debug_file}"
        insert_at = existing_insert_at if existing_insert_at is not None else _default_debug_insert_index(cleaned)
        if insert_at < 0:
            insert_at = 0
        if insert_at > len(cleaned):
            insert_at = len(cleaned)
        cleaned.insert(insert_at, debug_line)

    return "\n".join(cleaned).rstrip("\n") + "\n"

