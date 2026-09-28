"""Описание по-русски того, что под указателем мыши, и быстрые действия.

``describe_winws2_at`` отвечает на вопрос «что это за опция / функция /
аргумент». ``quick_actions_winws2`` — действия по месту курсора, не связанные
с конкретной ошибкой (исправления ошибок приходят вместе с проблемами).
"""

from __future__ import annotations

from .analysis import analyze_winws2_text
from .context import EMPTY_CONTEXT, LanguageContext
from .diagnostics import blob_insert_edit, known_blob_names
from .lua_catalog import LUA_ARGS, LUA_FUNCTIONS_BY_NAME, STD_SET_TITLES, function_arg_names, std_set_of
from .model import QuickFix, TextEdit
from .options import platform_note, resolve_windows_option
from .tokens import document_lines, line_tokens
from . import values as V
from fakes.public import BLOB_REFERENCE_ARG_NAMES

_ARG_TEXT = {
    "none": "без значения",
    "required": "значение обязательно: --опция=значение",
    "optional": "значение необязательно, только через «=»",
}
_SCOPE_TEXT = {
    "global": "общая опция — действует на весь запуск, место в начале пресета",
    "profile": "опция профиля — относится к текущему профилю (до следующего --new)",
    "any": "можно писать где угодно",
}


def _token_at(text: str, line: int, column: int):
    lines = document_lines(text)
    if not 0 <= line < len(lines):
        return None
    for token in line_tokens(lines[line], line):
        if token.start <= column < token.end:
            return token
    return None


def _describe_function(name: str) -> str:
    spec = LUA_FUNCTIONS_BY_NAME.get(name)
    if spec is None:
        return f"{name} — функция не из поставляемых lua-файлов."
    own = ", ".join(spec.args) if spec.args else "нет"
    std = ", ".join(STD_SET_TITLES.get(s, s) for s in spec.std)
    text = f"{spec.name} — {spec.summary}\nФайл: lua/{spec.files[0]}\nАргументы: {own}"
    if std:
        text += f"\nСтандартные наборы: {std}"
    return text


def _describe_arg(function: str, key: str) -> str:
    doc = LUA_ARGS.get(key)
    if doc is None:
        return f"{key} — аргумент, который читает сама функция или оркестратор."
    spec = LUA_FUNCTIONS_BY_NAME.get(function)
    note = ""
    if spec is not None and key not in spec.args:
        std_set = std_set_of(key)
        if std_set and key in function_arg_names(spec):
            note = f"\nСтандартный аргумент: {STD_SET_TITLES.get(std_set, std_set)}"
    return f"{key} — {doc.summary}{note}"


def describe_winws2_at(text: str, line: int, column: int, *, context: LanguageContext = EMPTY_CONTEXT) -> str:
    token = _token_at(text, line, column)
    if token is None or not token.text.startswith("-"):
        return ""
    dashes = 2 if token.text.startswith("--") else 1
    name, separator, value = token.text[dashes:].partition("=")
    spec, _ = resolve_windows_option(name)
    if spec is None:
        return ""
    offset = column - token.start
    name_end = dashes + len(name)
    if offset < name_end or not separator:
        lines = [f"{spec.flag} — {spec.summary}", _ARG_TEXT.get(spec.arg, ""), _SCOPE_TEXT.get(spec.scope, "")]
        note = platform_note(spec)
        if note:
            lines.append(f"Внимание: {note}")
        return "\n".join(line_text for line_text in lines if line_text)

    value_offset = offset - name_end - 1
    if spec.name == "lua-desync":
        call = V.parse_lua_call(value)
        if value_offset < call.function_end:
            return _describe_function(call.function)
        for arg in call.args:
            if arg.key_start <= value_offset < arg.key_end:
                return _describe_arg(call.function, arg.key)
            if arg.value is not None and arg.value_start <= value_offset < max(arg.value_end, arg.value_start + 1):
                if arg.key in BLOB_REFERENCE_ARG_NAMES:
                    return _describe_blob(text, arg.value, context)
                return _describe_arg(call.function, arg.key)
        return _describe_function(call.function)
    if spec.name == "blob":
        declaration = V.parse_blob_value(value)
        return f"Фейк «{declaration.name}» = {declaration.value}\n{spec.summary}"
    return f"{spec.flag} — {spec.summary}"


def _describe_blob(text: str, name: str, context: LanguageContext) -> str:
    analysis = analyze_winws2_text(context.preset_text if context.preset_text is not None else text)
    option = analysis.blobs.get(name)
    if option is not None and option.value:
        return f"Фейк «{name}» = {option.value.partition(':')[2]} (строка {option.token.line + 1})"
    if name in known_blob_names(analysis):
        return f"Фейк «{name}» — встроенный, объявлять не нужно."
    catalog = context.fake_values.get(name)
    if catalog:
        return f"Фейк «{name}» не объявлен. В реестре: {catalog}"
    return f"Фейк «{name}» не объявлен."


def quick_actions_winws2(
    text: str,
    line: int,
    column: int,
    *,
    fragment: bool = False,
    context: LanguageContext = EMPTY_CONTEXT,
) -> tuple[QuickFix, ...]:
    """Действия по месту курсора, не привязанные к одной ошибке."""
    if fragment:
        return ()
    analysis = analyze_winws2_text(text)
    actions: list[QuickFix] = []

    declared = known_blob_names(analysis)
    missing: list[str] = []
    for option in analysis.options_named("lua-desync"):
        call = V.parse_lua_call(option.value or "")
        if call.function == "tls_client_hello_clone":
            continue
        for arg in call.args:
            value = (arg.value or "").strip()
            if arg.key in BLOB_REFERENCE_ARG_NAMES and V.IDENTIFIER_RE.match(value) and value not in declared \
                    and value in context.fake_values and value not in missing:
                missing.append(value)
    if missing:
        lines_text = "\n".join(f"--blob={name}:{context.fake_values[name]}" for name in missing)
        actions.append(QuickFix(f"Объявить недостающие фейки ({len(missing)})",
                                (blob_insert_edit(analysis, lines_text),)))

    lines = document_lines(text)
    if 0 <= line < len(lines):
        column_end = len(lines[line])
        actions.append(QuickFix(
            "Вставить новый профиль ниже",
            (TextEdit(line, column_end, line, column_end,
                      "\n\n--new\n\n--name=Новый профиль\n--filter-tcp=443\n--lua-desync=pass"),),
        ))
    return tuple(actions)


__all__ = ["describe_winws2_at", "quick_actions_winws2"]
