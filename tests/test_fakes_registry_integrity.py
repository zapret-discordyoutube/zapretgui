"""Реестр фейков winws2 согласован с каталогами, пресетами и поставкой.

Реестр (private_zapretgui/resources/system/fakes_catalog.sqlite3) — это ответ
на вопрос «какой строкой --blob= объявляется фейк NAME». Проверяем:

- каждое имя blob-а, на которое ссылаются готовые стратегии winws2, builtin
  preset-ы winws2 и конфиг оркестратора (аргументы --lua-desync из
  BLOB_REFERENCE_ARG_NAMES), есть в реестре, среди встроенных blob-ов nfqws2
  или среди lua-имён блока --lua-init. Hex-литералы ``0x..`` — не имена.
  ``blob=`` у ``tls_client_hello_clone`` — не ссылка, а имя, которое функция
  создаёт сама; дальше в том же файле на него можно ссылаться;
- каждая строка ``--blob=NAME:VALUE`` в builtin preset-ах winws2 и в
  circular-config.txt совпадает со значением реестра для NAME.
  Допуск один: preset может указывать ДРУГОЙ файл из bin/, если его байты
  в точности равны файлу из реестра (например, старый
  ``@bin/quic_initial_4pda.to.bin`` при реестровом
  ``@bin/quic_initial_4pda_to.bin``). Hex сравнивается по байтам (регистр
  цифр не важен). Hex вместо файла и файл вместо hex не допускаются;
- имена реестра не совпадают со встроенными blob-ами nfqws2 (повтор —
  фатальная ошибка «duplicate blob name») и с верхнеуровневыми именами и
  функциями файлов --lua-init;
- каждый файл реестра есть в private dist/bin.

Без private-репозитория проверки пропускаются.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from fakes.public import (
    BLOB_REFERENCE_ARG_NAMES,
    NFQWS2_BUILTIN_BLOBS,
    FakesCatalog,
    load_fakes_catalog,
)
from profile.winws2_preset_source import WINWS2_LUA_INIT_PATHS

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PUBLIC_ROOT.parent / "private_zapretgui"
PRIVATE_DIST = PRIVATE_ROOT / "dist"
PRIVATE_BIN_DIR = PRIVATE_DIST / "bin"
REGISTRY_PATH = PRIVATE_ROOT / "resources" / "system" / "fakes_catalog.sqlite3"
CIRCULAR_CONFIG = PRIVATE_DIST / "lua" / "circular-config.txt"
CATALOGS_ROOT = PUBLIC_ROOT / "src" / "system" / "strategy_catalogs" / "winws2"
BUILTIN_PRESETS_ROOT = PUBLIC_ROOT / "src" / "presets" / "builtin" / "winws2"

# Функция, у которой blob= — имя создаваемого blob-а, а не ссылка.
_CLONE_FUNCTIONS = frozenset({"tls_client_hello_clone"})

# Одна строка может содержать несколько опций: «--a --b» (как при запуске).
_INLINE_OPTION_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=--)")
_LUA_FUNCTION_RE = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_LUA_TOP_LEVEL_GLOBAL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)", re.MULTILINE)
_INLINE_LUA_ASSIGN_RE = re.compile(r"(?:^|;)\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOB_DECL_RE = re.compile(r"^--blob=([^:]+):(.*)$")


def _options_from_text(text: str) -> list[str]:
    options: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("--"):
            options.extend(part.strip() for part in _INLINE_OPTION_SPLIT_RE.split(line))
    return options


def _options(path: Path) -> list[str]:
    return _options_from_text(path.read_text(encoding="utf-8", errors="replace"))


def _lua_init_names() -> set[str]:
    names: set[str] = set()
    for init_path in WINWS2_LUA_INIT_PATHS:
        text = (PRIVATE_DIST / init_path).read_text(encoding="utf-8", errors="replace")
        names |= set(_LUA_FUNCTION_RE.findall(text))
        names |= set(_LUA_TOP_LEVEL_GLOBAL_RE.findall(text))
    return names


def _blob_references(options: list[str]) -> tuple[set[str], set[str], set[str]]:
    """-> (ссылки на имена, имена от tls_client_hello_clone, непонятные значения)."""
    references: set[str] = set()
    created: set[str] = set()
    malformed: set[str] = set()
    for option in options:
        if not option.startswith("--lua-desync="):
            continue
        function, _, args = option[len("--lua-desync="):].partition(":")
        for part in args.split(":") if args else ():
            key, sep, value = part.partition("=")
            if not sep or key not in BLOB_REFERENCE_ARG_NAMES:
                continue
            if function in _CLONE_FUNCTIONS and key == "blob":
                created.add(value)
                continue
            if value.lower().startswith("0x"):
                continue
            if _NAME_RE.match(value):
                references.add(value)
            else:
                malformed.add(f"{key}={value}")
    return references, created, malformed


def _inline_lua_names(options: list[str]) -> set[str]:
    names: set[str] = set()
    for option in options:
        if option.startswith("--lua-init=") and not option[len("--lua-init="):].startswith("@"):
            names |= set(_INLINE_LUA_ASSIGN_RE.findall(option[len("--lua-init="):]))
    return names


def _blob_declarations(options: list[str]) -> list[tuple[str, str]]:
    return [
        (match.group(1), match.group(2))
        for option in options
        if (match := _BLOB_DECL_RE.match(option))
    ]


def _shipped_bins() -> dict[str, Path]:
    # Windows-first: поиск файла в bin/ не зависит от регистра.
    return {path.name.lower(): path for path in PRIVATE_BIN_DIR.iterdir() if path.is_file()}


def _value_bytes(value: str, shipped: dict[str, Path]) -> bytes | None:
    if value.startswith("@bin/"):
        path = shipped.get(value[len("@bin/"):].lower())
        return path.read_bytes() if path else None
    if value.lower().startswith("0x"):
        try:
            return bytes.fromhex(value[2:])
        except ValueError:
            return None
    return None


class _RegistryTestCase(unittest.TestCase):
    registry: FakesCatalog

    @classmethod
    def setUpClass(cls) -> None:
        if not REGISTRY_PATH.is_file() or not PRIVATE_BIN_DIR.is_dir():
            raise unittest.SkipTest(f"Нет private-репозитория: {PRIVATE_ROOT}")
        cls.registry = load_fakes_catalog(REGISTRY_PATH)

    def _declaration_problems(self, declarations: list[tuple[str, str]]) -> list[str]:
        shipped = _shipped_bins()
        problems: list[str] = []
        for name, value in declarations:
            entry = self.registry.entries.get(name)
            if entry is None:
                problems.append(f"{name}: нет в реестре ({value})")
                continue
            expected = entry.blob_value()
            if value == expected:
                continue
            same_source = value.startswith("@bin/") == expected.startswith("@bin/")
            actual_bytes = _value_bytes(value, shipped)
            if same_source and actual_bytes is not None and actual_bytes == _value_bytes(expected, shipped):
                continue
            problems.append(f"{name}: {value} != реестр {expected}")
        return problems


class RegistryShapeTest(_RegistryTestCase):
    def test_registry_names_do_not_shadow_engine_or_lua_names(self) -> None:
        names = set(self.registry.entries)
        self.assertEqual(sorted(names & NFQWS2_BUILTIN_BLOBS), [])
        self.assertEqual(sorted(names & _lua_init_names()), [])

    def test_every_registry_file_is_shipped(self) -> None:
        shipped = _shipped_bins()
        files = [entry.file_name for entry in self.registry.entries.values() if entry.file_name]
        self.assertTrue(files)
        missing = sorted(name for name in files if name.lower() not in shipped)
        self.assertEqual(missing, [], "нет файлов в private dist/bin")

    def test_same_bytes_note_matches_real_bytes(self) -> None:
        shipped = _shipped_bins()
        payloads = {
            name: _value_bytes(entry.blob_value(), shipped)
            for name, entry in self.registry.entries.items()
        }
        for name, entry in self.registry.entries.items():
            expected = sorted(o for o, data in payloads.items() if o != name and data == payloads[name])
            with self.subTest(name=name):
                self.assertEqual(entry.same_bytes_as, ",".join(expected) or None)


class BlobReferencesTest(_RegistryTestCase):
    def _check_sources(self, paths: list[Path]) -> None:
        self.assertTrue(paths)
        base = set(self.registry.entries) | NFQWS2_BUILTIN_BLOBS | _lua_init_names()
        total = 0
        for path in paths:
            options = _options(path)
            references, created, malformed = _blob_references(options)
            total += len(references)
            available = base | created | _inline_lua_names(options)
            with self.subTest(source=path.name):
                self.assertEqual(sorted(malformed), [], "значение blob-аргумента не имя и не 0x")
                self.assertEqual(sorted(references - available), [], "blob-а нет в реестре")
        self.assertGreater(total, 0)

    def test_winws2_catalog_references_are_known(self) -> None:
        self._check_sources(sorted(CATALOGS_ROOT.glob("*.txt")))

    def test_winws2_builtin_preset_references_are_known(self) -> None:
        self._check_sources(sorted(BUILTIN_PRESETS_ROOT.glob("*.txt")))

    def test_orchestra_config_references_are_known(self) -> None:
        self._check_sources([CIRCULAR_CONFIG])

    def test_clone_output_is_not_a_reference(self) -> None:
        references, created, malformed = _blob_references(
            _options_from_text(
                "--lua-desync=tls_client_hello_clone:blob=my_clone:sni_del "
                "--lua-desync=fake:blob=my_clone:repeats=2"
            )
        )
        self.assertEqual(created, {"my_clone"})
        self.assertEqual(references, {"my_clone"})
        self.assertEqual(malformed, set())

    def test_double_space_between_options_is_split(self) -> None:
        references, _created, malformed = _blob_references(
            _options_from_text("--lua-desync=syndata:blob=stun_pat  --lua-desync=fake:blob=tls7")
        )
        self.assertEqual(references, {"stun_pat", "tls7"})
        self.assertEqual(malformed, set())


class BlobDeclarationsTest(_RegistryTestCase):
    def test_builtin_preset_declarations_match_registry(self) -> None:
        checked = 0
        for path in sorted(BUILTIN_PRESETS_ROOT.glob("*.txt")):
            declarations = _blob_declarations(_options(path))
            checked += len(declarations)
            with self.subTest(preset=path.name):
                self.assertEqual(self._declaration_problems(declarations), [])
        self.assertGreater(checked, 0)

    def test_orchestra_config_declarations_match_registry(self) -> None:
        declarations = _blob_declarations(_options(CIRCULAR_CONFIG))
        self.assertTrue(declarations)
        self.assertEqual(self._declaration_problems(declarations), [])

    def test_builtin_names_are_never_declared(self) -> None:
        for path in [*sorted(BUILTIN_PRESETS_ROOT.glob("*.txt")), CIRCULAR_CONFIG]:
            names = {name for name, _value in _blob_declarations(_options(path))}
            with self.subTest(source=path.name):
                self.assertEqual(sorted(names & NFQWS2_BUILTIN_BLOBS), [])

    def test_hex_declared_where_registry_has_file_is_rejected(self) -> None:
        entry = next(e for e in self.registry.entries.values() if e.source_kind == "file")
        problems = self._declaration_problems([(entry.name, "0x00")])
        self.assertEqual(len(problems), 1)

    def test_different_bytes_file_is_rejected(self) -> None:
        entry = next(e for e in self.registry.entries.values() if e.name == "tls_google")
        problems = self._declaration_problems([(entry.name, "@bin/tls_clienthello_vk_com.bin")])
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
