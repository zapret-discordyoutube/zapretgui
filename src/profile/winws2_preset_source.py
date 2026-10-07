from __future__ import annotations

import re


WINWS2_LUA_INIT_PATHS: tuple[str, ...] = (
    "lua/zapret-lib.lua",
    "lua/zapret-antidpi.lua",
    "lua/zapret-auto.lua",
    "lua/custom_funcs.lua",
    "lua/custom_diag.lua",
    "lua/zapret-multishake.lua",
    "lua/fakemultisplit.lua",
    "lua/fakemultidisorder.lua",
)

WINWS2_LUA_INIT_LINES: tuple[str, ...] = tuple(
    f"--lua-init=@{lua_path}" for lua_path in WINWS2_LUA_INIT_PATHS
)

_LUA_INIT_OPTION = "--lua-init="
# Та же граница, по которой запуск делит строку «--a --b» на отдельные аргументы
# (winws_runtime.runners.preset_runner_support._INLINE_ARG_SPLIT_RE).
_INLINE_OPTION_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=--)")
_STRATEGY_TAG_RE = re.compile(r":strategy=\d+", re.IGNORECASE)
# Оркестраторы, которые сами выбирают среди инстансов с меткой :strategy=N.
_CIRCULAR_LUA_DESYNC_RE = re.compile(
    r"(?<!\S)--lua-desync=(?:circular|shadow_probe)(?::\S*)?(?=\s|$)", re.IGNORECASE
)


def is_winws2_circular_preset_source(source_text: str) -> bool:
    for raw in str(source_text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _CIRCULAR_LUA_DESYNC_RE.search(stripped):
            return True
    return False


def has_winws2_strategy_tags(source_text: str) -> bool:
    for raw in str(source_text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("--lua-desync=") and _STRATEGY_TAG_RE.search(stripped):
            return True
    return False


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def canonical_winws2_lua_init_path(option: str) -> str | None:
    """Какой файл обязательного блока подключает опция ``--lua-init=...``.

    Возвращает путь из ``WINWS2_LUA_INIT_PATHS`` или ``None``, если опция
    подключает что-то другое. Равнозначными считаются написания, которые winws2
    откроет как тот же файл: любой регистр, обратные слэши, кавычки вокруг
    значения и ``@lua/<файл>`` против абсолютного ``@C:/.../lua/<файл>``.
    ``--lua-init=`` без ``@`` — это lua-код прямо в пресете, а не файл, поэтому
    такая опция никогда не считается частью блока.
    """
    text = str(option or "").strip()
    if not text.lower().startswith(_LUA_INIT_OPTION):
        return None
    value = _strip_wrapping_quotes(text[len(_LUA_INIT_OPTION):])
    if not value.startswith("@"):
        return None
    path = _strip_wrapping_quotes(value[1:]).replace("\\", "/").lower()
    for lua_path in WINWS2_LUA_INIT_PATHS:
        if path == lua_path or path.endswith(f"/{lua_path}"):
            return lua_path
    return None


def _drop_block_options(stripped_line: str) -> tuple[bool, str]:
    """Убирает из строки опции, которые подключают файлы обязательного блока.

    Возвращает (строка менялась, что от неё осталось). Строка с несколькими
    опциями («--a --b») делится так же, как её делит запуск.
    """
    parts = [part for part in _INLINE_OPTION_SPLIT_RE.split(stripped_line) if part.strip()]
    kept = [part for part in parts if canonical_winws2_lua_init_path(part) is None]
    if len(kept) == len(parts):
        return False, stripped_line
    return True, " ".join(kept)


def ensure_winws2_lua_init_block(source_text: str) -> str:
    """Гарантирует обязательный блок ``--lua-init`` в тексте пресета winws2.

    Формат пресета winws2 требует полный блок ``WINWS2_LUA_INIT_LINES`` в этом
    порядке. Блок ставится в начало преамбулы — сразу после ведущих строк-
    комментариев шапки (граница шапки та же, что у ``profile.parser``), то есть
    никогда не внутрь profile. Строки, которые подключают те же файлы в другом
    написании или в другом месте текста, убираются, чтобы файл не грузился
    дважды. Остальные ``--lua-init`` пользователя (другие файлы, lua-код прямо
    в пресете) остаются на своих местах, после блока. Функция идемпотентна.
    """
    from profile.parser import normalize_text, parse_preset_text
    from settings.mode import ENGINE_WINWS2

    text = normalize_text(source_text)
    lines = text.split("\n")
    header_end = len(parse_preset_text(text, engine=ENGINE_WINWS2).header_lines)

    body: list[str] = []
    for raw in lines[header_end:]:
        stripped = raw.strip()
        if stripped.startswith("--"):
            changed, remainder = _drop_block_options(stripped)
            if changed:
                if remainder:
                    body.append(remainder)
                continue
        body.append(raw)

    insert_at = header_end
    if not any(line.strip() for line in body):
        # В тексте одна шапка: блок идёт сразу после последнего комментария,
        # а не после хвостовых пустых строк.
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
        body = [*lines[insert_at:header_end], *body]

    return "\n".join([*lines[:insert_at], *WINWS2_LUA_INIT_LINES, *body])


__all__ = [
    "WINWS2_LUA_INIT_LINES",
    "WINWS2_LUA_INIT_PATHS",
    "canonical_winws2_lua_init_path",
    "ensure_winws2_lua_init_block",
    "has_winws2_strategy_tags",
    "is_winws2_circular_preset_source",
]
