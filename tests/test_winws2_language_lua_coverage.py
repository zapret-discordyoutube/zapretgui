"""Справочник Lua-функций редактора покрывает ВСЕ функции поставляемых lua-файлов.

Для каждого файла ``lua/*.lua`` из сборки берутся функции вида
``function имя(ctx, desync)`` (их можно вызвать через ``--lua-desync``) и
ключи ``desync.arg.X``, которые функция читает. Каждая такая функция должна
быть в справочнике с правильным файлом, а каждый ключ — среди её аргументов,
иначе редактор не подскажет его и может ошибочно ругаться.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SRC = PUBLIC_ROOT / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

LUA_DIR = PUBLIC_ROOT.parent / "private_zapretgui" / "dist" / "lua"

_DESYNC_FUNCTION_RE = re.compile(r"^function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*ctx\s*,\s*desync\s*\)", re.M)
_ANY_FUNCTION_RE = re.compile(r"^(?:local\s+)?function\s+", re.M)
_ARG_RE = re.compile(
    r"\bdesync\.arg\.([A-Za-z_][A-Za-z0-9_]*)|\bdesync\.arg\[\s*\"([A-Za-z_][A-Za-z0-9_]*)\"\s*\]"
)


def desync_functions(lua_dir: Path) -> dict[str, tuple[set[str], set[str]]]:
    """{функция: (файлы, прочитанные ключи desync.arg)}."""
    found: dict[str, tuple[set[str], set[str]]] = {}
    for path in sorted(lua_dir.glob("*.lua")):
        text = path.read_text(encoding="utf-8", errors="replace")
        starts = [match.start() for match in _ANY_FUNCTION_RE.finditer(text)] + [len(text)]
        for match in _DESYNC_FUNCTION_RE.finditer(text):
            end = min(start for start in starts if start > match.start())
            keys = {a or b for a, b in _ARG_RE.findall(text[match.start():end])}
            files, arg_keys = found.setdefault(match.group(1), (set(), set()))
            files.add(path.name)
            arg_keys.update(keys)
    return found


class Winws2LuaCatalogTests(unittest.TestCase):
    def test_every_argument_of_every_function_is_documented(self) -> None:
        from profile.winws2_language.lua_catalog import LUA_ARGS, LUA_FUNCTIONS, function_arg_names

        for spec in LUA_FUNCTIONS:
            with self.subTest(function=spec.name):
                self.assertTrue(spec.summary.strip())
                self.assertTrue(spec.files)
                for name in function_arg_names(spec):
                    self.assertIn(name, LUA_ARGS)

    def test_mandatory_lua_init_files_are_known(self) -> None:
        from profile.winws2_language.lua_catalog import KNOWN_LUA_FILES
        from profile.winws2_preset_source import WINWS2_LUA_INIT_PATHS

        for path in WINWS2_LUA_INIT_PATHS:
            self.assertIn(Path(path).name, KNOWN_LUA_FILES)

    @unittest.skipUnless(LUA_DIR.is_dir(), "нет поставляемых lua-файлов")
    def test_catalog_covers_every_shipped_desync_function_and_argument(self) -> None:
        from profile.winws2_language.lua_catalog import LUA_FUNCTIONS_BY_NAME, function_arg_names

        functions = desync_functions(LUA_DIR)
        self.assertGreater(len(functions), 100)
        for name, (files, keys) in functions.items():
            with self.subTest(function=name):
                spec = LUA_FUNCTIONS_BY_NAME.get(name)
                self.assertIsNotNone(spec, f"{name} нет в справочнике")
                self.assertTrue(files <= set(spec.files), f"{name}: файлы {files} vs {spec.files}")
                missing = keys - set(function_arg_names(spec))
                self.assertEqual(missing, set(), f"{name}: не описаны аргументы {sorted(missing)}")

    @unittest.skipUnless(LUA_DIR.is_dir(), "нет поставляемых lua-файлов")
    def test_catalog_has_no_functions_missing_from_shipped_files(self) -> None:
        from profile.winws2_language.lua_catalog import LUA_FUNCTIONS

        functions = desync_functions(LUA_DIR)
        extra = [spec.name for spec in LUA_FUNCTIONS if spec.name not in functions]
        self.assertEqual(extra, [])


if __name__ == "__main__":
    unittest.main()
