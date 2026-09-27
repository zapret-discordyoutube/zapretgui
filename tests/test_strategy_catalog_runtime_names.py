"""Имена из готовых стратегий должны существовать в поставке.

Каталоги стратегий и builtin preset-ы ссылаются на lua-функции (`--lua-desync=`),
blob-ы (`blob=`, `seqovl_pattern=` ...) и файлы фейков в `bin/`. Если имени нет
в поставке, winws2/winws падает при запуске уже у пользователя. Lua-файлы и
bin-файлы лежат в private-репозитории (private_zapretgui/dist), поэтому без
него проверка пропускается.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from fakes.names import BLOB_REFERENCE_ARG_NAMES, NFQWS2_BUILTIN_BLOBS
from profile.winws2_preset_source import WINWS2_LUA_INIT_PATHS

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIST = PUBLIC_ROOT.parent / "private_zapretgui" / "dist"
PRIVATE_LUA_DIR = PRIVATE_DIST / "lua"
PRIVATE_BIN_DIR = PRIVATE_DIST / "bin"
CATALOGS_ROOT = PUBLIC_ROOT / "src" / "system" / "strategy_catalogs"
BUILTIN_PRESETS_ROOT = PUBLIC_ROOT / "src" / "presets" / "builtin"


_LUA_FUNCTION_RE = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_LUA_TOP_LEVEL_GLOBAL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", re.MULTILINE)
_INLINE_LUA_ASSIGN_RE = re.compile(r"(?:^|;)\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_LUA_DESYNC_RE = re.compile(r"^--lua-desync=([A-Za-z0-9_]+)(.*)$")
_BLOB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PRESET_BLOB_RE = re.compile(r"^--blob=([A-Za-z0-9_]+):(.*)$")
_WINWS1_BIN_VALUE_RE = re.compile(r"^--dpi-desync[a-z0-9-]*=(.+)$")
_WINWS1_PRESET_BIN_RE = re.compile(r"(?<![A-Za-z0-9_])bin[/\\]([A-Za-z0-9_.\-]+\.bin)", re.IGNORECASE)


def _lua_file(init_path: str) -> Path:
    # "lua/zapret-lib.lua" в preset-е -> private_zapretgui/dist/lua/zapret-lib.lua
    return PRIVATE_DIST / Path(init_path)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _option_lines(path: Path):
    for raw in _read(path).splitlines():
        line = raw.strip()
        if line.startswith("--"):
            yield line


def _catalog_files(engine: str) -> list[Path]:
    return sorted((CATALOGS_ROOT / engine).glob("*.txt"))


def _preset_files(engine: str) -> list[Path]:
    return sorted((BUILTIN_PRESETS_ROOT / engine).glob("*.txt"))


def _shipped_bin_names() -> set[str]:
    # Windows-first: поиск файла в bin/ не зависит от регистра.
    return {path.name.lower() for path in PRIVATE_BIN_DIR.iterdir() if path.is_file()}


def _bin_file_name(value: str) -> str | None:
    """`+2@bin/x.bin`, `@x.bin`, `bin/x.bin`, `x.bin` -> `x.bin`; не файл -> None."""
    text = re.sub(r"^\+\d+", "", value.strip()).lstrip("@")
    if not text.lower().endswith(".bin"):
        return None
    return re.split(r"[/\\]", text)[-1]


class _PrivateDistTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if not PRIVATE_LUA_DIR.is_dir() or not PRIVATE_BIN_DIR.is_dir():
            self.skipTest(f"Нет private-репозитория: {PRIVATE_DIST}")


class LuaDesyncFunctionsTest(_PrivateDistTestCase):
    def _available_functions(self) -> set[str]:
        # Каждый preset winws2 подключает полный обязательный блок --lua-init,
        # поэтому доступны функции всех его файлов.
        functions: set[str] = set()
        for init_path in WINWS2_LUA_INIT_PATHS:
            functions |= set(_LUA_FUNCTION_RE.findall(_read(_lua_file(init_path))))
        return functions

    def test_full_lua_init_block_defines_each_function_once(self) -> None:
        # Блок грузится целиком: одно имя в двух файлах молча перетёрло бы
        # функцию из файла, подключённого раньше.
        owners: dict[str, list[str]] = {}
        for init_path in WINWS2_LUA_INIT_PATHS:
            for name in set(_LUA_FUNCTION_RE.findall(_read(_lua_file(init_path)))):
                owners.setdefault(name, []).append(init_path)
        duplicates = {name: paths for name, paths in owners.items() if len(paths) > 1}
        self.assertEqual(duplicates, {})

    def test_lua_init_files_define_each_function_once(self) -> None:
        # Повторное `function name(` молча перетирает первое определение.
        for init_path in WINWS2_LUA_INIT_PATHS:
            names = _LUA_FUNCTION_RE.findall(_read(_lua_file(init_path)))
            with self.subTest(init_path=init_path):
                duplicates = sorted({name for name in names if names.count(name) > 1})
                self.assertEqual(duplicates, [], f"{init_path}: функции объявлены дважды")

    def test_lua_init_files_exist(self) -> None:
        for init_path in WINWS2_LUA_INIT_PATHS:
            with self.subTest(init_path=init_path):
                self.assertTrue(_lua_file(init_path).is_file(), init_path)

    def test_every_catalog_lua_desync_function_is_defined(self) -> None:
        available = self._available_functions()
        for path in _catalog_files("winws2"):
            used = {
                match.group(1)
                for line in _option_lines(path)
                if (match := _LUA_DESYNC_RE.match(line))
            }
            with self.subTest(catalog=path.name):
                self.assertTrue(used)
                self.assertEqual(sorted(used - available), [], f"winws2/{path.name}: нет lua-функций")


class Winws2BlobNamesTest(_PrivateDistTestCase):
    def _available_blobs(self) -> set[str]:
        names = set(NFQWS2_BUILTIN_BLOBS)
        for init_path in WINWS2_LUA_INIT_PATHS:
            names |= set(_LUA_TOP_LEVEL_GLOBAL_RE.findall(_read(_lua_file(init_path))))
        for path in _preset_files("winws2"):
            for line in _option_lines(path):
                if match := _PRESET_BLOB_RE.match(line):
                    names.add(match.group(1))
                elif line.lower().startswith("--lua-init=") and not line[len("--lua-init="):].startswith("@"):
                    # Короткий lua-код прямо в preset-е: --lua-init=fake_zero64=string.rep(...)
                    names |= set(_INLINE_LUA_ASSIGN_RE.findall(line[len("--lua-init="):]))
        return names

    def test_every_catalog_blob_name_is_defined(self) -> None:
        available = self._available_blobs()
        for path in _catalog_files("winws2"):
            used: set[str] = set()
            for line in _option_lines(path):
                match = _LUA_DESYNC_RE.match(line)
                if not match:
                    continue
                for part in match.group(2).split(":"):
                    key, sep, value = part.partition("=")
                    if not sep or key not in BLOB_REFERENCE_ARG_NAMES:
                        continue
                    if value.lower().startswith("0x") or not _BLOB_NAME_RE.match(value):
                        continue
                    used.add(value)
            with self.subTest(catalog=path.name):
                self.assertEqual(sorted(used - available), [], f"winws2/{path.name}: нет blob-ов")


class ShippedBinFilesTest(_PrivateDistTestCase):
    def test_winws1_catalog_bin_files_exist(self) -> None:
        shipped = _shipped_bin_names()
        checked = 0
        for path in _catalog_files("winws1"):
            used: set[str] = set()
            for line in _option_lines(path):
                match = _WINWS1_BIN_VALUE_RE.match(line)
                if not match:
                    continue
                for value in match.group(1).split(","):
                    file_name = _bin_file_name(value)
                    if file_name:
                        used.add(file_name)
            checked += len(used)
            with self.subTest(catalog=path.name):
                missing = sorted(name for name in used if name.lower() not in shipped)
                self.assertEqual(missing, [], f"winws1/{path.name}: нет файлов в dist/bin")
        self.assertGreater(checked, 0)

    def test_winws1_builtin_preset_bin_files_exist(self) -> None:
        shipped = _shipped_bin_names()
        checked = 0
        for path in _preset_files("winws1"):
            used = set(_WINWS1_PRESET_BIN_RE.findall(_read(path)))
            checked += len(used)
            with self.subTest(preset=path.name):
                missing = sorted(name for name in used if name.lower() not in shipped)
                self.assertEqual(missing, [], f"winws1/{path.name}: нет файлов в dist/bin")
        self.assertGreater(checked, 0)

    def test_winws2_builtin_preset_blob_files_exist(self) -> None:
        shipped = _shipped_bin_names()
        checked = 0
        for path in _preset_files("winws2"):
            used: set[str] = set()
            for line in _option_lines(path):
                match = _PRESET_BLOB_RE.match(line)
                if not match or not match.group(2).lstrip("+0123456789").startswith("@"):
                    continue
                file_name = _bin_file_name(match.group(2))
                if file_name:
                    used.add(file_name)
            checked += len(used)
            with self.subTest(preset=path.name):
                missing = sorted(name for name in used if name.lower() not in shipped)
                self.assertEqual(missing, [], f"winws2/{path.name}: нет файлов в dist/bin")
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
