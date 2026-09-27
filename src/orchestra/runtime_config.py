"""Сборка рабочей копии конфига оркестратора (circular-config.runtime.txt).

Файл поставки ``lua/circular-config.txt`` только читается: он входит в
манифест целостности установки, и любая запись в него выглядит как порча.
Всё, что оркестратору нужно поверх поставки, попадает только в рабочую копию.

Почему дополнительные опции пишутся внутрь копии, а не в командную строку:
winws2, запущенный как ``winws2 @file``, читает опции только из файла и
молча игнорирует всё остальное в argv (zapret2 nfqws.c, manual.md
``@<config_file>``). Поэтому предзагрузка выученных стратегий
(``--lua-init``) и вывод отладки в stdout (``--debug=1``) должны стоять в
самом файле.
"""

from __future__ import annotations

from utils.circular_strategy_numbering import renumber_circular_strategies


# Разбор строк LOCK/SUCCESS/FAIL в OrchestraRunner._read_output читает
# отладочный вывод winws2 из stdout — без --debug=1 этих строк нет.
ORCHESTRA_DEBUG_OPTION = "--debug=1"

_BOM = "﻿"


def _is_debug_option(stripped: str) -> bool:
    return stripped == "--debug" or stripped.startswith("--debug=")


def build_orchestra_runtime_config(source_text: str, *, learned_lua_arg: str | None = None) -> str:
    """Возвращает текст рабочего конфига оркестратора.

    - служебные теги ``:strategy=N`` расставляются заново по порядку;
    - строки-комментарии ``#`` убираются: winws2 разбирает файл через
      wordexp, а в комментариях поставки есть скобки;
    - ``--debug`` задаётся ровно один раз, первой строкой, как ``--debug=1``;
    - ``--lua-init=<learned_lua_arg>`` встаёт сразу после последнего
      ``--lua-init`` поставки: выученные стратегии вызывают функции,
      объявленные в этих Lua-файлах.

    ``learned_lua_arg`` — уже готовое для wordexp значение (путь в кавычках
    при необходимости), без префикса ``@``.
    """
    text = str(source_text or "")
    if text.startswith(_BOM):
        text = text[len(_BOM):]

    lines: list[str] = []
    for raw in renumber_circular_strategies(text).splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if _is_debug_option(stripped):
            continue
        lines.append(raw)

    if learned_lua_arg:
        learned_line = f"--lua-init=@{learned_lua_arg}"
        last_init = -1
        for index, raw in enumerate(lines):
            if raw.strip().startswith("--lua-init="):
                last_init = index
        lines.insert(last_init + 1, learned_line)

    return "\n".join([ORCHESTRA_DEBUG_OPTION, *lines]) + "\n"


__all__ = ["ORCHESTRA_DEBUG_OPTION", "build_orchestra_runtime_config"]
