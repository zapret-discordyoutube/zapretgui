"""Проверка текста пресета winws2: где ошибка и как её исправить.

Серьёзность:

- ``error`` — winws2 не запустится, программа не даст запустить пресет или
  строка точно не делает то, что написано (необъявленный фейк, функция из
  неподключённого lua-файла);
- ``warning`` — запустится, но скорее всего работает не так, как задумано;
- ``hint`` — мелочь или стиль.

Проверка только читает текст: исправления (``fixes``) применяются лишь по
явному выбору пользователя, как обычная правка. Пресет остаётся точкой истины
(см. ``presets.preset_contract``).
"""

from __future__ import annotations

import difflib
import re

from fakes.public import BLOB_REFERENCE_ARG_NAMES, NFQWS2_BUILTIN_BLOBS
from profile.preset_blob_declarations import LUA_DEFINED_BLOB_NAMES
from profile.winws2_preset_source import is_winws2_circular_preset_source

from .analysis import ParsedOption, PresetAnalysis, analyze_winws2_text
from .context import EMPTY_CONTEXT, LanguageContext
from .file_refs import file_references
from .lua_catalog import (
    KNOWN_LUA_FILES,
    LUA_ARGS,
    LUA_FUNCTIONS_BY_NAME,
    ORCHESTRATOR_LABELS,
    closest_lua_function_names,
    function_arg_names,
    ignored_arg_names,
)
from .model import (
    SEVERITY_ERROR,
    SEVERITY_HINT,
    SEVERITY_WARNING,
    Diagnostic,
    QuickFix,
    TextEdit,
    replace_in_line,
)
from .options import (
    EXITING_OPTION_NAMES,
    OPTIONS_BY_NAME,
    WINDOWS_OPTIONS,
    platform_note,
)
from .tokens import Token, document_lines
from . import values as V

# Опции, которых программа требует для запуска (profile.launch_validation).
_GUI_FILTER_OPTIONS = ("wf-tcp-out", "wf-udp-out", "wf-raw-part")
# Состояние, которое действует на следующие --lua-desync (не «последняя побеждает»).
_SEQUENTIAL_OPTIONS = frozenset({"payload", "in-range", "out-range"})
# Функция, у которой blob= — имя, под которым она САМА сохраняет результат.
_BLOB_OUTPUT_SLOT_FUNCTIONS = frozenset({"tls_client_hello_clone"})
_NOFAKE_RE = re.compile(r"^nofake\d+$")


class _Collector:
    def __init__(self, analysis: PresetAnalysis) -> None:
        self.analysis = analysis
        self.lines = document_lines(analysis.text)
        self.items: list[Diagnostic] = []

    def add(self, line, start, end, severity, message, fixes=()) -> None:
        end = max(int(end), int(start) + 1)
        self.items.append(Diagnostic(int(line), int(start), end, severity, message, tuple(fixes)))

    def on_token(self, token: Token, severity: str, message: str, fixes=()) -> None:
        self.add(token.line, token.start, token.end, severity, message, fixes)

    def on_name(self, option: ParsedOption, severity: str, message: str, fixes=()) -> None:
        self.add(option.token.line, option.name_start, option.name_end, severity, message, fixes)

    def on_value(self, option: ParsedOption, severity: str, message: str, fixes=(), *, start=None, end=None) -> None:
        if option.value_line < 0:
            self.on_name(option, severity, message, fixes)
            return
        begin = option.value_start if start is None else start
        finish = option.value_end if end is None else end
        self.add(option.value_line, begin, finish, severity, message, fixes)

    def line_end_insert(self, line: int, text: str) -> TextEdit:
        column = len(self.lines[line]) if 0 <= line < len(self.lines) else 0
        return TextEdit(line, column, line, column, f"\n{text}")

    def remove_line_fix(self, token: Token, title: str = "Удалить строку") -> QuickFix:
        line = token.line
        if line + 1 < len(self.lines):
            return QuickFix(title, (TextEdit(line, 0, line + 1, 0, ""),))
        if line > 0:
            return QuickFix(title, (TextEdit(line - 1, len(self.lines[line - 1]), line, len(self.lines[line]), ""),))
        return QuickFix(title, (TextEdit(line, 0, line, len(self.lines[line]), ""),))


def _closest(word: str, candidates, *, n: int = 3, cutoff: float = 0.6) -> list[str]:
    return difflib.get_close_matches(str(word or ""), list(candidates), n=n, cutoff=cutoff)


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = count % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


# ---------------------------------------------------------------- синтаксис


def _check_syntax(c: _Collector) -> None:
    analysis = c.analysis
    if analysis.end_of_options is not None:
        c.on_token(
            analysis.end_of_options,
            SEVERITY_ERROR,
            "«--» без имени — конец опций: всё, что ниже, winws2 пропустит.",
            (c.remove_line_fix(analysis.end_of_options),),
        )

    for token in analysis.stray_tokens:
        c.on_token(
            token,
            SEVERITY_WARNING,
            "Строка не начинается с «--»: winws2 её пропустит. Если это комментарий — начните строку с #.",
            (QuickFix("Сделать комментарием", (replace_in_line(token.line, token.start, token.start, "# "),)),),
        )

    for option, lost in analysis.lost_values:
        join = TextEdit(option.token.line, option.token.end, lost.line, lost.start, "=")
        c.on_token(
            lost,
            SEVERITY_ERROR,
            f"Значение потеряется: у {option.spec.flag} оно пишется только через «=» "
            f"({option.spec.flag}={lost.text}).",
            (QuickFix(f"Записать как {option.spec.flag}={lost.text}", (join,)),),
        )

    for option in analysis.options:
        _check_option_syntax(c, option)


def _check_option_syntax(c: _Collector, option: ParsedOption) -> None:
    token = option.token
    name = option.written_name
    spec = option.spec
    if spec is None:
        if option.candidates:
            fixes = tuple(
                QuickFix(f"Заменить на {candidate.flag}",
                         (replace_in_line(token.line, option.name_start, option.name_end, candidate.flag),))
                for candidate in option.candidates[:5]
            )
            c.on_name(
                option,
                SEVERITY_ERROR,
                f"«{name}» подходит сразу к нескольким опциям: "
                + ", ".join(candidate.flag for candidate in option.candidates[:6])
                + ". Напишите имя полностью.",
                fixes,
            )
            return
        known = OPTIONS_BY_NAME.get(name)
        if known is not None:
            c.on_name(
                option,
                SEVERITY_ERROR,
                f"{known.flag} {platform_note(known)} — winws2 для Windows её не знает и не запустится.",
                (c.remove_line_fix(token),),
            )
            return
        if " " in name or "\t" in name:
            head = name.split()[0]
            head_spec = OPTIONS_BY_NAME.get(head)
            if head_spec is not None and head_spec.available_on_windows:
                space_at = token.start + option.dashes + len(head)
                fixed = TextEdit(token.line, space_at, token.line,
                                 space_at + len(name) - len(head) - len(name[len(head):].lstrip()), "=")
                c.on_name(
                    option,
                    SEVERITY_ERROR,
                    f"Значение через пробел: winws2 прочитает всю строку как одну неизвестную опцию. "
                    f"Пишите {head_spec.flag}=значение.",
                    (QuickFix(f"Поставить «=» после {head_spec.flag}", (fixed,)),),
                )
                return
        suggestions = _closest(name, [spec.name for spec in WINDOWS_OPTIONS])
        fixes = tuple(
            QuickFix(f"Заменить на --{suggestion}",
                     (replace_in_line(token.line, option.name_start, option.name_end, f"--{suggestion}"),))
            for suggestion in suggestions
        )
        hint = f" Возможно, имелось в виду --{suggestions[0]}." if suggestions else ""
        c.on_name(option, SEVERITY_ERROR, f"Неизвестная опция «--{name}»: winws2 не запустится.{hint}", fixes)
        return

    if name != spec.name:
        c.on_name(
            option,
            SEVERITY_WARNING,
            f"Сокращённое имя: winws2 поймёт его как {spec.flag}, а программа — нет. Напишите полностью.",
            (QuickFix(f"Заменить на {spec.flag}",
                      (replace_in_line(token.line, option.name_start, option.name_end, spec.flag),)),),
        )
    elif option.dashes == 1:
        c.on_name(
            option,
            SEVERITY_WARNING,
            f"Один дефис: winws2 примет, но программа такую строку не поймёт. Пишите {spec.flag}.",
            (QuickFix(f"Заменить на {spec.flag}",
                      (replace_in_line(token.line, option.name_start, option.name_end, spec.flag),)),),
        )

    has_equals = option.value is not None and option.value_token is None
    if spec.arg == "none" and has_equals:
        c.add(
            token.line,
            option.name_end,
            token.end,
            SEVERITY_ERROR,
            f"У {spec.flag} не бывает значения — winws2 не запустится.",
            (QuickFix("Убрать значение", (replace_in_line(token.line, option.name_end, token.end, ""),)),),
        )
    elif spec.arg == "required" and option.value is None:
        c.on_name(
            option,
            SEVERITY_ERROR,
            f"Нет значения: пишите {spec.flag}=значение." + (f" Например: {spec.flag}={spec.placeholder}"
                                                             if spec.placeholder else ""),
            (QuickFix("Дописать «=»", (replace_in_line(token.line, token.end, token.end, "="),)),),
        )
    elif option.value_token is not None:
        following = option.value_token
        join = TextEdit(token.line, token.end, following.line, following.start, "=")
        if following.text.startswith("-"):
            c.on_name(
                option,
                SEVERITY_ERROR,
                f"Нет значения после {spec.flag}: winws2 заберёт следующий аргумент «{following.text}» "
                "как значение, и он пропадёт.",
                (QuickFix("Дописать «=»", (replace_in_line(token.line, token.end, token.end, "="),)),),
            )
        else:
            c.on_name(
                option,
                SEVERITY_WARNING,
                f"Значение на отдельной строке: winws2 поймёт, а программа — нет. "
                f"Пишите {spec.flag}={following.text}.",
                (QuickFix(f"Соединить в {spec.flag}={following.text}", (join,)),),
            )


# ------------------------------------------------------------------ значения


def _check_values(c: _Collector, context: LanguageContext, known_blobs: set[str], loaded_files, lua_known: bool,
                  circular: bool) -> None:
    templates_seen: set[str] = set()
    for option in c.analysis.options:
        spec = option.spec
        if spec is None:
            continue
        if spec.name in {"template", "name"} and option.value:
            if spec.name == "template" or any(
                o.spec is not None and o.spec.name == "template"
                for o in c.analysis.profiles[option.profile_index].options
            ):
                templates_seen.add(option.value)
        if option.value is None:
            continue
        value = option.value
        kind = spec.value
        message = ""
        severity = SEVERITY_ERROR
        if kind == "ports":
            message = V.check_ports(value)
        elif kind == "icmp":
            message = V.check_icmp(value)
        elif kind == "ipp":
            message = V.check_ipp(value)
        elif kind == "l3":
            message = V.check_l3(value)
        elif kind == "l7_list":
            message = V.check_l7_list(value)
        elif kind == "payload_list":
            message = V.check_payload_list(value) if value else ""
        elif kind == "range":
            message = V.check_range(value)
        elif kind == "debug":
            message = V.check_debug(value)
        elif kind == "ctrack_timeouts":
            message = V.check_ctrack_timeouts(value)
        elif kind == "int":
            message = V.check_int(value, minimum=spec.int_min, maximum=spec.int_max)
        elif kind == "bool01":
            message = V.check_bool01(value)
            severity = SEVERITY_WARNING
        elif kind == "wf_iface":
            message = V.check_wf_iface(value)
        elif kind in {"file", "path", "wf_filter", "lua_init"}:
            if not value.strip() or value.strip() == "@":
                message = "Не указан файл."
        elif kind == "template":
            if value not in templates_seen:
                message = (
                    f"Шаблон «{value}» не объявлен выше. Сначала профиль с --template и --name={value}, "
                    "затем --import."
                )
        elif kind == "blob":
            _check_blob_option(c, option)
        elif kind == "lua_desync":
            _check_lua_desync(c, option, context, known_blobs, loaded_files, lua_known, circular)
        if message:
            fixes = ()
            if kind in {"payload_list", "l7_list"}:
                names = V.PAYLOAD_NAMES if kind == "payload_list" else V.L7_PROTO_NAMES
                bad = [item for item in value.split(",") if item.strip().lower() not in names]
                if len(bad) == 1:
                    fixes = tuple(
                        QuickFix(f"Заменить «{bad[0]}» на «{suggestion}»",
                                 (replace_in_line(option.value_line,
                                                  option.value_start + value.index(bad[0]),
                                                  option.value_start + value.index(bad[0]) + len(bad[0]),
                                                  suggestion),))
                        for suggestion in _closest(bad[0].strip().lower(), names)
                    )
            c.on_value(option, severity, message, fixes)


def _check_blob_option(c: _Collector, option: ParsedOption) -> None:
    declaration = V.parse_blob_value(option.value or "")
    if declaration.error:
        c.on_value(option, SEVERITY_ERROR, declaration.error)
        return
    if declaration.name in NFQWS2_BUILTIN_BLOBS:
        c.on_value(
            option,
            SEVERITY_ERROR,
            f"«{declaration.name}» — встроенный фейк winws2, объявлять его нельзя: будет ошибка «duplicate blob name».",
            (c.remove_line_fix(option.token),),
            end=option.value_start + len(declaration.name),
        )
        return
    first = c.analysis.blobs.get(declaration.name)
    if first is not None and first is not option:
        c.on_value(
            option,
            SEVERITY_ERROR,
            f"Фейк «{declaration.name}» уже объявлен в строке {first.token.line + 1} — winws2 не запустится "
            "(duplicate blob name).",
            (c.remove_line_fix(option.token, "Удалить повторное объявление"),),
            end=option.value_start + len(declaration.name),
        )


# ---------------------------------------------------------------- Lua-вызовы


def _lua_init_insert_fix(c: _Collector, file_name: str) -> QuickFix:
    lua_inits = c.analysis.options_named("lua-init")
    line_text = f"--lua-init=@lua/{file_name}"
    if lua_inits:
        edit = c.line_end_insert(lua_inits[-1].token.line, line_text)
    else:
        first_line = c.analysis.tokens[0].line if c.analysis.tokens else 0
        edit = TextEdit(first_line, 0, first_line, 0, f"{line_text}\n")
    return QuickFix(f"Подключить {line_text}", (edit,))


def blob_insert_edit(analysis: PresetAnalysis, block_text: str) -> TextEdit:
    """Куда вставить новые строки ``--blob=``: после последнего фейка, иначе
    после блока ``--lua-init``, иначе в начало."""
    lines = document_lines(analysis.text)

    def after(line: int) -> TextEdit:
        column = len(lines[line]) if 0 <= line < len(lines) else 0
        return TextEdit(line, column, line, column, f"\n{block_text}")

    blobs = analysis.options_named("blob")
    if blobs:
        return after(blobs[-1].token.line)
    anchors = analysis.options_named("lua-init")
    if anchors:
        return after(anchors[-1].token.line)
    first_line = analysis.tokens[0].line if analysis.tokens else 0
    return TextEdit(first_line, 0, first_line, 0, f"{block_text}\n")


def blob_declaration_fix(c: _Collector, name: str, value: str) -> QuickFix:
    line_text = f"--blob={name}:{value}"
    return QuickFix(f"Объявить {line_text}", (blob_insert_edit(c.analysis, line_text),))


def _check_lua_desync(c: _Collector, option: ParsedOption, context: LanguageContext, known_blobs: set[str],
                      loaded_files, lua_known: bool, circular: bool) -> None:
    call = V.parse_lua_call(option.value or "")
    base = option.value_start
    line = option.value_line
    if call.error:
        c.add(line, base + call.error_start, base + call.error_end, SEVERITY_ERROR,
              f"{call.error} winws2 не запустится (invalid lua function call).")
        return
    spec = LUA_FUNCTIONS_BY_NAME.get(call.function)
    function_span = (base + call.function_start, base + call.function_end)
    if spec is None:
        if lua_known:
            suggestions = closest_lua_function_names(call.function)
            fixes = tuple(
                QuickFix(f"Заменить на {name}", (replace_in_line(line, *function_span, name),))
                for name in suggestions
            )
            hint = f" Возможно, имелось в виду {suggestions[0]}." if suggestions else ""
            c.add(line, *function_span, SEVERITY_ERROR,
                  f"Функции «{call.function}» нет в подключённых lua-файлах — стратегия не сработает.{hint}",
                  fixes)
        return
    if not any(name.lower() in loaded_files for name in spec.files):
        if lua_known or c.analysis.fragment:
            severity = SEVERITY_ERROR if not c.analysis.fragment or context.preset_text is not None else SEVERITY_HINT
            fixes = () if c.analysis.fragment else (_lua_init_insert_fix(c, spec.files[0]),)
            c.add(line, *function_span, severity,
                  f"Функция {call.function} лежит в lua/{spec.files[0]}, но этот файл не подключён через --lua-init.",
                  fixes)

    accepted = set(function_arg_names(spec))
    ignored = ignored_arg_names(spec)
    for arg in call.args:
        key_span = (base + arg.key_start, base + arg.key_end)
        value_span = (base + arg.value_start, base + max(arg.value_end, arg.value_start))
        if arg.key == "strategy" and not circular:
            c.add(line, *key_span, SEVERITY_ERROR,
                  ":strategy=N работает только в circular-пресете (с --lua-desync=circular) — "
                  "программа не запустит такой пресет.")
        if arg.key in ignored:
            c.add(line, *key_span, SEVERITY_WARNING,
                  f"{call.function} не использует IP-фрагментацию: аргумент {arg.key} ни на что не влияет.")
            continue
        if arg.key not in accepted and arg.key not in ORCHESTRATOR_LABELS and not _NOFAKE_RE.match(arg.key):
            suggestions = _closest(arg.key, accepted, cutoff=0.75)
            if suggestions:
                c.add(line, *key_span, SEVERITY_WARNING,
                      f"{call.function} не знает аргумент «{arg.key}». Возможно, опечатка: {suggestions[0]}.",
                      tuple(QuickFix(f"Заменить на {name}", (replace_in_line(line, *key_span, name),))
                            for name in suggestions))
            elif spec.strict_args:
                c.add(line, *key_span, SEVERITY_HINT,
                      f"{call.function} не читает аргумент «{arg.key}» — он ни на что не влияет.")
            continue
        _check_lua_arg_value(c, call.function, arg, line, key_span, value_span)

    if call.function in _BLOB_OUTPUT_SLOT_FUNCTIONS:
        return
    has_optional = any(arg.key == "optional" for arg in call.args)
    for arg in call.args:
        if arg.key not in BLOB_REFERENCE_ARG_NAMES or not arg.value:
            continue
        value = arg.value.strip().strip('"').strip("'")
        if value[:2].lower() == "0x" or not V.IDENTIFIER_RE.match(value) or value in known_blobs:
            continue
        span = (base + arg.value_start, base + arg.value_end)
        if c.analysis.fragment and context.preset_text is None:
            continue
        fixes = ()
        catalog_value = context.fake_values.get(value)
        if catalog_value and not c.analysis.fragment:
            fixes = (blob_declaration_fix(c, value, catalog_value),)
        if has_optional:
            c.add(line, *span, SEVERITY_WARNING,
                  f"Фейк «{value}» не объявлен: из-за optional функция просто ничего не отправит.", fixes)
        else:
            where = " Он есть в реестре фейков — объявление можно добавить." if catalog_value else ""
            c.add(line, *span, SEVERITY_ERROR,
                  f"Фейк «{value}» не объявлен через --blob= — функция не сработает.{where}", fixes)


def _check_lua_arg_value(c: _Collector, function: str, arg, line: int, key_span, value_span) -> None:
    doc = LUA_ARGS.get(arg.key)
    if doc is None:
        return
    value = arg.value
    message = ""
    if doc.kind == "int":
        if value is None or value == "":
            message = f"{arg.key}: нужно число, например {arg.key}=2."
        elif not re.match(r"^[+-]?\d+$", value):
            message = f"{arg.key}: «{value}» — не число."
    elif doc.kind == "pos":
        if value is not None:
            message = V.check_pos_list(value)
    elif doc.kind == "tls_mod":
        if value is not None:
            message = V.check_tls_mod(value)
    elif doc.kind == "payload":
        if value is not None:
            message = V.check_lua_payload(value)
    elif doc.kind == "enum":
        if value is not None and doc.values and value not in doc.values:
            message = f"{arg.key}: «{value}» — допустимо {', '.join(doc.values)}."
    elif doc.kind == "blob":
        if value is None or value == "":
            message = f"{arg.key}: нужно имя фейка из --blob= или 0xHEX."
    if not message:
        return
    target = value_span if value else key_span
    fixes = ()
    if doc.kind in {"enum", "payload", "pos", "tls_mod"} and value:
        candidates = doc.values or (V.PAYLOAD_NAMES if doc.kind == "payload" else ())
        fixes = tuple(
            QuickFix(f"Заменить на {option}", (replace_in_line(line, *value_span, option),))
            for option in _closest(value, candidates)
        )
    c.add(line, *target, SEVERITY_WARNING, f"{message} winws2 запустится, но {function} будет работать неверно.",
          fixes)


# ------------------------------------------------------------- весь пресет


def _check_structure(c: _Collector, circular: bool) -> None:
    analysis = c.analysis
    seen_global: dict[str, ParsedOption] = {}
    for profile in analysis.profiles:
        seen_profile: dict[str, ParsedOption] = {}
        last_lua_desync = -1
        for position, option in enumerate(profile.options):
            if option.spec is not None and option.spec.name == "lua-desync":
                last_lua_desync = position
        for position, option in enumerate(profile.options):
            spec = option.spec
            if spec is None:
                continue
            if spec.name in EXITING_OPTION_NAMES:
                c.on_name(option, SEVERITY_ERROR,
                          f"С {spec.flag} winws2 сразу завершится — обход не запустится.",
                          (c.remove_line_fix(option.token),))
            if analysis.fragment and spec.scope == "global":
                c.on_name(option, SEVERITY_WARNING,
                          f"{spec.flag} — общая опция: она действует на весь запуск, а не на этот профиль. "
                          "Её место — в начале пресета.")
            elif spec.scope == "global" and profile.index > 0:
                c.on_name(option, SEVERITY_WARNING,
                          f"{spec.flag} — общая опция внутри профиля: winws2 применит её ко всему запуску. "
                          "Перенесите в начало пресета.")
            if spec.name in _SEQUENTIAL_OPTIONS and position > last_lua_desync and not analysis.fragment:
                c.on_name(option, SEVERITY_WARNING,
                          f"После {spec.flag} в этом профиле нет --lua-desync — строка ни на что не влияет.")
            if spec.repeat == "last" and spec.name not in _SEQUENTIAL_OPTIONS:
                store = seen_global if spec.scope == "global" else seen_profile
                previous = store.get(spec.name)
                if previous is not None:
                    c.on_name(previous, SEVERITY_WARNING,
                              f"{spec.flag} повторяется ниже (строка {option.token.line + 1}) — "
                              "действует только последнее значение.",
                              (c.remove_line_fix(previous.token, "Удалить эту строку"),))
                store[spec.name] = option
            if spec.repeat == "once":
                previous = seen_profile.get(spec.name)
                if previous is not None:
                    c.on_name(option, SEVERITY_ERROR,
                              f"Второй {spec.flag} в одном профиле — winws2 не запустится.",
                              (c.remove_line_fix(option.token),))
                seen_profile[spec.name] = option

    raw = analysis.options_named("wf-raw")
    if raw:
        for option in analysis.options:
            if option.spec is not None and option.spec.name.startswith("wf-") and option.spec.name in {
                "wf-tcp-in", "wf-tcp-out", "wf-udp-in", "wf-udp-out", "wf-icmp-in", "wf-icmp-out",
                "wf-ipp-in", "wf-ipp-out", "wf-raw-part", "wf-raw-filter",
            }:
                c.on_name(option, SEVERITY_WARNING,
                          "При --wf-raw эта опция не действует: --wf-raw задаёт фильтр целиком.")

    if analysis.fragment or not analysis.tokens:
        return

    if not any(analysis.options_named(name) for name in _GUI_FILTER_OPTIONS):
        first = analysis.tokens[0].line if analysis.tokens else 0
        c.add(first, 0, max(1, len(c.lines[first]) if c.lines else 1), SEVERITY_ERROR,
              "Нет фильтра перехвата: программа не запустит пресет без --wf-tcp-out, --wf-udp-out "
              "или --wf-raw-part.",
              (QuickFix("Добавить --wf-tcp-out=80,443 и --wf-udp-out=443",
                        (TextEdit(first, 0, first, 0, "--wf-tcp-out=80,443\n--wf-udp-out=443\n"),)),))

    enabled = [
        profile for profile in analysis.profiles
        if not profile.skipped and any(
            o.spec is not None and o.spec.scope == "profile" and o.spec.name != "new" for o in profile.options
        )
    ]
    if analysis.profiles and not enabled and analysis.tokens:
        last = analysis.tokens[-1]
        c.on_token(last, SEVERITY_ERROR,
                   "В пресете нет ни одного включённого профиля — программа его не запустит.")

    for profile in analysis.profiles:
        if profile.index == 0:
            continue
        if not any(o.spec is not None and o.spec.name != "new" for o in profile.options):
            new_option = profile.options[0]
            c.on_name(new_option, SEVERITY_HINT, "Пустой профиль: после --new ничего нет.",
                      (c.remove_line_fix(new_option.token),))


def _check_files(c: _Collector, context: LanguageContext) -> None:
    facts = context.file_facts
    if facts is None:
        return
    for reference in file_references(c.analysis):
        status = facts.status(reference.path)
        if status is False:
            expected = facts.expected_path(reference.path)
            c.add(reference.line, reference.start, reference.end, SEVERITY_ERROR,
                  f"Файл не найден: {expected or reference.path}. winws2 не запустится.")


# ------------------------------------------------------------------ вход


def known_blob_names(analysis: PresetAnalysis, context_analysis: PresetAnalysis | None = None) -> set[str]:
    names = set(NFQWS2_BUILTIN_BLOBS) | set(LUA_DEFINED_BLOB_NAMES)
    for source in (analysis, context_analysis):
        if source is None:
            continue
        names.update(source.blobs)
        for option in source.options_named("lua-desync"):
            call = V.parse_lua_call(option.value or "")
            if call.function in _BLOB_OUTPUT_SLOT_FUNCTIONS:
                names.update(arg.value for arg in call.args if arg.key == "blob" and arg.value)
    return names


def diagnose_winws2_text(
    text: str,
    *,
    fragment: bool = False,
    context: LanguageContext = EMPTY_CONTEXT,
) -> tuple[Diagnostic, ...]:
    analysis = analyze_winws2_text(text, fragment=fragment)
    context_analysis = (
        analyze_winws2_text(context.preset_text) if fragment and context.preset_text is not None else None
    )
    lua_source = context_analysis if context_analysis is not None else analysis
    loaded_files = lua_source.loaded_lua_files
    lua_known = not lua_source.lua_init_unknown and all(
        name.lower() in {known.lower() for known in KNOWN_LUA_FILES} for name in lua_source.lua_init_files
    )
    circular = is_winws2_circular_preset_source(analysis.text) or (
        context_analysis is not None and is_winws2_circular_preset_source(context.preset_text or "")
    )

    collector = _Collector(analysis)
    _check_syntax(collector)
    _check_values(collector, context, known_blob_names(analysis, context_analysis), loaded_files, lua_known,
                  circular)
    _check_structure(collector, circular)
    _check_files(collector, context)
    order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_HINT: 2}
    return tuple(sorted(collector.items, key=lambda d: (d.line, d.start, order.get(d.severity, 3))))


def count_by_severity(diagnostics) -> dict[str, int]:
    counts = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 0, SEVERITY_HINT: 0}
    for item in diagnostics or ():
        counts[item.severity] = counts.get(item.severity, 0) + 1
    return counts


def problems_summary_text(diagnostics) -> str:
    counts = count_by_severity(diagnostics)
    errors, warnings = counts[SEVERITY_ERROR], counts[SEVERITY_WARNING]
    parts = []
    if errors:
        parts.append(f"{errors} {_plural(errors, 'ошибка', 'ошибки', 'ошибок')}")
    if warnings:
        parts.append(f"{warnings} {_plural(warnings, 'предупреждение', 'предупреждения', 'предупреждений')}")
    return ", ".join(parts)


__all__ = [
    "blob_declaration_fix",
    "blob_insert_edit",
    "count_by_severity",
    "diagnose_winws2_text",
    "known_blob_names",
    "problems_summary_text",
]
