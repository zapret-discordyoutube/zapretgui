"""Подсказки при наборе текста пресета winws2 (автодополнение).

По месту курсора определяется, что сейчас пишется: имя опции, значение опции,
имя Lua-функции в ``--lua-desync``, её аргумент или значение аргумента, и
предлагаются только подходящие варианты. Отбор по уже набранному началу
делается здесь же — редактор только показывает список.
"""

from __future__ import annotations

import re

from fakes.public import NFQWS2_BUILTIN_BLOBS
from profile.preset_blob_declarations import LUA_DEFINED_BLOB_NAMES

from .analysis import analyze_winws2_text
from .context import EMPTY_CONTEXT, LanguageContext
from .diagnostics import known_blob_names
from .lua_catalog import (
    LUA_ARGS,
    LUA_FUNCTIONS,
    LUA_FUNCTIONS_BY_NAME,
    STD_SET_TITLES,
    function_arg_names,
    std_set_of,
)
from .model import CompletionItem, CompletionResult
from .options import OptionSpec, WINDOWS_OPTIONS, resolve_windows_option
from .tokens import document_lines, line_tokens
from . import values as V

_MAX_ITEMS = 300
_WORD_TAIL_RE = re.compile(r"^[A-Za-z0-9_\-.]*")
_REOPEN_ARG_KINDS = frozenset({"blob", "pos", "payload", "enum", "callback", "tls_mod"})
_RANGE_EXAMPLES = (
    ("-d8", "первые 8 пакетов с данными"),
    ("-d10", "первые 10 пакетов с данными"),
    ("-n8", "первые 8 пакетов"),
    ("-n3", "первые 3 пакета"),
    ("a", "все пакеты"),
    ("x", "ни одного пакета"),
    ("n2-n5", "со 2-го по 5-й пакет"),
    ("s1<s2000", "первые 2000 байт потока"),
)
_FILE_FOLDERS = {
    "hostlist": ("lists", ""),
    "hostlist-exclude": ("lists", ""),
    "hostlist-auto": ("lists", ""),
    "ipset": ("lists", ""),
    "ipset-exclude": ("lists", ""),
    "lua-init": ("lua", "@"),
    "wf-raw": ("windivert.filter", "@"),
    "wf-raw-part": ("windivert.filter", "@"),
    "wf-raw-filter": ("windivert.filter", "@"),
}
_SCOPE_TEXT = {"global": "общая", "profile": "профиль", "any": ""}
# Что чаще всего пишут в пресетах — в начале списка; служебные опции,
# после которых winws2 сразу выходит, — в самом конце.
_POPULAR_OPTIONS = (
    "new", "name", "filter-tcp", "filter-udp", "filter-l7", "hostlist", "hostlist-domains",
    "hostlist-exclude", "ipset", "ipset-exclude", "payload", "out-range", "in-range", "lua-desync", "skip",
    "wf-tcp-out", "wf-udp-out", "wf-raw-part", "lua-init", "blob", "ctrack-disable",
    "ipcache-lifetime", "ipcache-hostname", "debug",
)
_RARE_OPTIONS = frozenset({"version", "dry-run", "fuzz", "daemon", "wf-save", "nlm-list", "pidfile"})


def _filter(items: list[CompletionItem], prefix: str) -> tuple[CompletionItem, ...]:
    needle = str(prefix or "").lower()
    if not needle:
        return tuple(items[:_MAX_ITEMS])
    starts = [item for item in items if item.label.lower().lstrip("-@").startswith(needle.lstrip("-@"))
              or item.label.lower().startswith(needle)]
    seen = {id(item) for item in starts}
    contains = [item for item in items if id(item) not in seen and needle.lstrip("-@") in item.label.lower()]
    return tuple((starts + contains)[:_MAX_ITEMS])


def _word_end(line_text: str, column: int) -> int:
    match = _WORD_TAIL_RE.match(line_text[column:])
    return column + (match.end() if match else 0)


def _option_items(analysis, line: int, *, fragment: bool) -> list[CompletionItem]:
    first_profile_line = None
    if not fragment:
        for option in analysis.options:
            spec = option.spec
            if spec is not None and spec.scope == "profile":
                first_profile_line = option.token.line
                break
    prefer_global = not fragment and (first_profile_line is None or line <= first_profile_line)
    order = ("global", "any", "profile") if prefer_global else ("profile", "any", "global")

    def item(spec: OptionSpec) -> CompletionItem:
        scope = _SCOPE_TEXT.get(spec.scope, "")
        detail = f"{spec.summary}" + (f" · {scope}" if scope else "")
        if spec.arg == "required":
            return CompletionItem(spec.flag, f"{spec.flag}=", detail, "option", reopen=_has_value_items(spec))
        return CompletionItem(spec.flag, spec.flag, detail, "option")

    popularity = {name: index for index, name in enumerate(_POPULAR_OPTIONS)}
    scope_rank = {scope: index for index, scope in enumerate(order)}
    specs = sorted(
        WINDOWS_OPTIONS,
        key=lambda spec: (
            spec.name in _RARE_OPTIONS,
            scope_rank.get(spec.scope, 3),
            popularity.get(spec.name, len(popularity)),
        ),
    )
    items = [item(spec) for spec in specs]
    if not fragment:
        items.append(
            CompletionItem(
                "--new (новый профиль)",
                "--new\n--name=Новый профиль\n--filter-tcp=443\n--lua-desync=",
                "Заготовка профиля: --new, имя, порты и стратегия",
                "snippet",
                reopen=True,
            )
        )
    return items


def _has_value_items(spec: OptionSpec) -> bool:
    return bool(spec.values) or spec.value in {
        "payload_list", "l7_list", "l3", "range", "lua_desync", "blob", "template", "debug", "bool01",
    } or spec.name in _FILE_FOLDERS


def _list_items(names, used: set[str], detail: str = "") -> list[CompletionItem]:
    return [CompletionItem(name, name, detail, "value") for name in names if name not in used]


def _complete_list(value_prefix: str, value_start: int, names, detail: str = "") -> tuple[int, str, list]:
    comma = value_prefix.rfind(",")
    item_prefix = value_prefix[comma + 1:]
    used = {part.strip() for part in value_prefix[:comma].split(",")} if comma >= 0 else set()
    return value_start + comma + 1, item_prefix, _list_items(names, used, detail)


def _file_items(folder: str, at: str, context: LanguageContext) -> list[CompletionItem]:
    facts = context.file_facts
    if facts is None:
        return []
    return [
        CompletionItem(f"{at}{folder}/{name}", f"{at}{folder}/{name}", "файл программы", "file")
        for name in facts.listings.get(folder, ())
    ]


def _blob_name_items(analysis, context_analysis, context: LanguageContext) -> list[CompletionItem]:
    declared = known_blob_names(analysis, context_analysis)
    items: list[CompletionItem] = []
    for name in sorted(declared):
        if name in NFQWS2_BUILTIN_BLOBS:
            detail = "встроенный фейк winws2"
        elif name in LUA_DEFINED_BLOB_NAMES:
            detail = "фейк из custom_funcs.lua"
        else:
            source = (context_analysis or analysis).blobs.get(name) or analysis.blobs.get(name)
            detail = f"объявлен: {source.value.partition(':')[2]}" if source is not None and source.value else "объявлен"
        items.append(CompletionItem(name, name, detail, "value"))
    for name in sorted(context.fake_values):
        if name not in declared:
            items.append(CompletionItem(
                name, name, f"реестр фейков, ещё не объявлен (Ctrl+. добавит --blob=): {context.fake_values[name]}",
                "value",
            ))
    return items


def _complete_lua_desync(value_prefix: str, value_start: int, analysis, context_analysis,
                         context: LanguageContext) -> tuple[int, str, list]:
    colon_positions = [m.start() for m in re.finditer(r"(?<!\\):", value_prefix)]
    loaded = (context_analysis or analysis).loaded_lua_files
    if not colon_positions:
        items = []
        for spec in sorted(LUA_FUNCTIONS, key=lambda s: (not any(f.lower() in loaded for f in s.files), s.name)):
            available = any(f.lower() in loaded for f in spec.files)
            detail = spec.summary if available else f"{spec.summary} · нужен --lua-init=@lua/{spec.files[0]}"
            items.append(CompletionItem(spec.name, spec.name, detail, "function"))
        return value_start, value_prefix, items

    function = value_prefix[:colon_positions[0]]
    segment_start = colon_positions[-1] + 1
    segment = value_prefix[segment_start:]
    spec = LUA_FUNCTIONS_BY_NAME.get(function)
    if "=" in segment:
        key, _, arg_prefix = segment.partition("=")
        arg_value_start = value_start + segment_start + len(key) + 1
        doc = LUA_ARGS.get(key)
        if doc is None:
            return arg_value_start, arg_prefix, []
        if key in {"blob", "fake_blob", "fallback", "seqovl_pattern", "pattern", "white_blob"}:
            return arg_value_start, arg_prefix, _blob_name_items(analysis, context_analysis, context)
        if doc.kind == "payload":
            body_offset = 1 if arg_prefix.startswith("~") else 0
            start, prefix, items = _complete_list(arg_prefix[body_offset:], arg_value_start + body_offset,
                                                  V.PAYLOAD_NAMES)
            return start, prefix, items
        if doc.kind in {"pos", "tls_mod"}:
            return _complete_list(arg_prefix, arg_value_start, doc.values)
        if doc.values:
            return arg_value_start, arg_prefix, _list_items(doc.values, set())
        return arg_value_start, arg_prefix, []

    used = {part.partition("=")[0] for part in re.split(r"(?<!\\):", value_prefix)[1:-1]}
    names = function_arg_names(spec) if spec is not None else tuple(sorted(LUA_ARGS))
    items = []
    for name in names:
        if name in used:
            continue
        doc = LUA_ARGS.get(name)
        if doc is None:
            continue
        std_set = std_set_of(name) if spec is not None and name not in spec.args else ""
        detail = doc.summary + (f" · стандартный: {STD_SET_TITLES.get(std_set, std_set)}" if std_set else "")
        if doc.kind == "flag":
            items.append(CompletionItem(name, name, detail, "argument"))
        else:
            items.append(CompletionItem(name, f"{name}=", detail, "argument", reopen=doc.kind in _REOPEN_ARG_KINDS))
    return value_start + segment_start, segment, items


def _complete_value(spec: OptionSpec, value_prefix: str, value_start: int, analysis, context_analysis,
                    context: LanguageContext) -> tuple[int, str, list]:
    kind = spec.value
    if kind == "payload_list":
        return _complete_list(value_prefix, value_start, V.PAYLOAD_NAMES)
    if kind == "l7_list":
        return _complete_list(value_prefix, value_start, V.L7_PROTO_NAMES)
    if kind == "l3":
        return _complete_list(value_prefix, value_start, ("ipv4", "ipv6"))
    if kind == "range":
        return value_start, value_prefix, [CompletionItem(v, v, d, "value") for v, d in _RANGE_EXAMPLES]
    if kind == "lua_desync":
        return _complete_lua_desync(value_prefix, value_start, analysis, context_analysis, context)
    if kind == "template":
        names = sorted((context_analysis or analysis).templates)
        return value_start, value_prefix, _list_items(names, set(), "шаблон")
    if kind == "blob":
        name, colon, tail = value_prefix.partition(":")
        if colon:
            return (value_start + len(name) + 1, tail,
                    _file_items("bin", "@", context))
        declared = set((context_analysis or analysis).blobs)
        items = [
            CompletionItem(fake, f"{fake}:{value}", f"реестр фейков: {value}", "value")
            for fake, value in sorted(context.fake_values.items())
            if fake not in declared
        ]
        return value_start, value_prefix, items
    if spec.name in _FILE_FOLDERS:
        folder, at = _FILE_FOLDERS[spec.name]
        return value_start, value_prefix, _file_items(folder, at, context)
    return value_start, value_prefix, [CompletionItem(v, v, "", "value") for v in spec.values]


def complete_winws2(
    text: str,
    line: int,
    column: int,
    *,
    fragment: bool = False,
    context: LanguageContext = EMPTY_CONTEXT,
    explicit: bool = False,
) -> CompletionResult | None:
    lines = document_lines(text)
    if not 0 <= line < len(lines):
        return None
    line_text = lines[line].rstrip("\r")
    column = max(0, min(int(column), len(line_text)))
    before = line_text[:column]
    if before.strip().startswith("#"):
        return None

    tokens = line_tokens(before, line)
    current = tokens[-1] if tokens and tokens[-1].end == column else None
    if current is None and not explicit:
        return None
    if current is not None and not current.text.startswith("-"):
        return None
    # Весь пресет разбирается только здесь, когда курсор точно в опции.
    analysis = analyze_winws2_text(text, fragment=fragment)
    context_analysis = (
        analyze_winws2_text(context.preset_text) if fragment and context.preset_text is not None else None
    )

    if current is None:
        items = _option_items(analysis, line, fragment=fragment)
        return CompletionResult(line, column, column, _filter(items, ""))

    token_text = current.text
    dashes = 2 if token_text.startswith("--") else 1
    body = token_text[dashes:]
    if "=" not in body:
        items = _option_items(analysis, line, fragment=fragment)
        end = _word_end(line_text, column)
        return CompletionResult(line, current.start, end, _filter(items, token_text))

    name, _, value_prefix = body.partition("=")
    spec, _candidates = resolve_windows_option(name)
    if spec is None:
        return None
    value_start = current.start + dashes + len(name) + 1
    start, prefix, items = _complete_value(spec, value_prefix, value_start, analysis, context_analysis, context)
    if not items:
        return None
    end = _word_end(line_text, column)
    filtered = _filter(items, prefix)
    if end < len(line_text) and line_text[end] == "=":
        # «=» уже написан: не удваивать его при вставке аргумента.
        filtered = tuple(
            CompletionItem(i.label, i.insert_text[:-1], i.detail, i.kind, False) if i.insert_text.endswith("=") else i
            for i in filtered
        )
    return CompletionResult(line, start, end, filtered)


__all__ = ["complete_winws2"]
