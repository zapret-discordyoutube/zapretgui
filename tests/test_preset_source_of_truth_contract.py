"""Пресет — точка истины: сохранение меняет текст только разрешёнными способами.

Разрешённые преобразования перечислены в presets.preset_contract. Тест
«save-identity» сравнивает то, что попало на диск, с тем, что отдали на
сохранение, после стирания РОВНО этих разрешённых различий: любое другое
изменение текста (потерянная строка, переставленные опции, скрытая добавка)
делает стёртые формы разными. Отдельно проверяется, что обязательный блок
--lua-init стоит один раз и в начале преамбулы.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.paths import AppPaths
from presets.file_service import PresetFileService
from presets.file_store import PresetFileStore
from presets.preset_contract import (
    DEBUG_LOG_DIR,
    SAVE_TIME_NORMALIZATIONS,
    SERVICE_HEADER_PREFIXES,
    normalize_preset_source_for_save,
)
from profile.parser import parse_preset_text
from profile.winws2_preset_source import (
    WINWS2_LUA_INIT_LINES,
    canonical_winws2_lua_init_path,
    ensure_winws2_lua_init_block,
)
from settings.mode import ENGINE_WINWS1, ENGINE_WINWS2, ZAPRET1_MODE, ZAPRET2_MODE


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_ROOT = PUBLIC_ROOT / "src" / "presets" / "builtin"
LAUNCH_METHOD_BY_ENGINE = {ENGINE_WINWS2: ZAPRET2_MODE, ENGINE_WINWS1: ZAPRET1_MODE}
# Граница «--a --b» та же, что у запуска (preset_runner_support).
_OPTION_SPLIT_RE = re.compile(r"(?<=\S)\s+(?=--)")


def _options(line: str) -> list[str]:
    return [part for part in _OPTION_SPLIT_RE.split(line.strip()) if part]


_EXP_INLINE = "--lua-init=fake_unknown_256=string.rep(string.char(0),256);fake_zero64=string.rep(string.char(0),64)"

# Тексты, на которых старая «добавь только нужные lua-init» логика ломалась:
# вставка внутрь profile, дубли при другом написании пути, неполный блок.
WINWS2_FIXTURES: dict[str, str] = {
    "no_block": (
        "# Preset: No block\n"
        "\n"
        "--wf-tcp-out=443\n"
        "--new\n"
        "--name=youtube\n"
        "--filter-tcp=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
    "partial_block": (
        "# Preset: Partial\n"
        "# Description: two lines only\n"
        "\n"
        "--lua-init=@lua/zapret-lib.lua\n"
        "--lua-init=@lua/zapret-antidpi.lua\n"
        "\n"
        "--wf-tcp-out=443\n"
        "--filter-tcp=443\n"
        "--lua-desync=multisplit:pos=1\n"
    ),
    "other_spellings": (
        "# Preset: Spellings\n"
        "--LUA-INIT=@LUA/ZAPRET-LIB.LUA\n"
        "--lua-init=@lua\\zapret-antidpi.lua\n"
        '--lua-init="@C:\\Program Files\\Zapret\\lua\\zapret-auto.lua"\n'
        "--lua-init=@'lua/custom_funcs.lua'\n"
        "--lua-init=@./lua/custom_diag.lua\n"
        "--wf-tcp-out=443\n"
        "--filter-tcp=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
    "lua_init_inside_profile": (
        "# Preset: Inside profile\n"
        "--wf-tcp-out=443\n"
        "--new\n"
        "--name=discord\n"
        "--filter-tcp=443\n"
        "--lua-init=@lua/fakemultisplit.lua\n"
        "--lua-desync=fakemultisplit:pos=1\n"
        "--new\n"
        "--name=youtube\n"
        "--filter-tcp=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
    "duplicates": (
        "--lua-init=@lua/zapret-lib.lua\n"
        "--lua-init=@lua/zapret-lib.lua\n"
        "--lua-init=@lua/zapret-lib.lua --lua-init=@lua/my_extra.lua\n"
        "--wf-tcp-out=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
    "exp_inline_code": (
        "# Preset: EXP\n"
        "\n"
        "--lua-init=@lua/zapret-lib.lua\n"
        "--lua-init=@lua/zapret-antidpi.lua\n"
        f"{_EXP_INLINE}\n"
        "\n"
        "--wf-tcp-out=443\n"
        "--lua-desync=fake:blob=fake_unknown_256\n"
    ),
    "no_at_is_inline_code": (
        "# Preset: No at\n"
        "--lua-init=lua/zapret-lib.lua\n"
        "--wf-tcp-out=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
    "crlf_service_headers_legacy_debug": (
        "# Preset: Legacy\r\n"
        "# ActivePreset: Legacy\r\n"
        "# Modified: 2026-01-21T17:52:34\r\n"
        "--wf-tcp-out=443\r\n"
        "--debug=@logs/Legacy_debug.log\r\n"
        "--lua-desync=fake:blob=tls_google\r\n"
        "\r\n\r\n"
    ),
    "header_only": "# Preset: Empty\n\n",
    "no_header_first_line_is_profile": (
        "--filter-tcp=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
}

# Только через сохранение: метку BOM снимает нормализация сохранения (и чтение
# файла как utf-8-sig), а не функция блока --lua-init.
WINWS2_SAVE_ONLY_FIXTURES: dict[str, str] = {
    "bom_first_line": (
        "\ufeff# Preset: Bom\n"
        "--wf-tcp-out=443\n"
        "--lua-desync=fake:blob=tls_google\n"
    ),
}

WINWS1_FIXTURES: dict[str, str] = {
    "crlf_and_headers": (
        "# Preset: Old\r\n"
        "# Modified: yesterday\r\n"
        "--wf-tcp=443\r\n"
        "--filter-tcp=443\r\n"
        "--dpi-desync=fake,split2"
    ),
    "legacy_debug": "--wf-tcp=443\n--debug=@logs/old.log\n--dpi-desync=fake\n",
}


def _builtin_texts(engine: str) -> dict[str, str]:
    return {
        path.name: path.read_bytes().decode("utf-8")
        for path in sorted((BUILTIN_ROOT / engine).glob("*.txt"))
    }


def _erase_allowed_save_differences(text: str, engine: str) -> list[str]:
    """Стирает ровно разрешённые преобразования сохранения (presets.preset_contract).

    Если после стирания исходный и сохранённый тексты различаются, сохранение
    сделало что-то сверх договора.
    """
    lines: list[str] = []
    # Метка BOM — кодировка файла, а не текст пресета.
    text = text[1:] if text.startswith("\ufeff") else text
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        lowered = stripped.lower()
        # Шапка имени/типа, служебные строки # Modified: / # ActivePreset:.
        if lowered.startswith(("# preset:", "# presetkind:", *SERVICE_HEADER_PREFIXES)):
            continue
        # Перенос старого --debug=@logs/... в user/logs.
        if lowered.startswith("--debug=@logs/"):
            raw = f"--debug=@{DEBUG_LOG_DIR}/{stripped.split('=', 1)[1][len('@logs/'):]}"
        if engine == ENGINE_WINWS2 and stripped.startswith("--"):
            # Обязательный блок --lua-init: его файлы в любом написании стираются.
            parts = _options(stripped)
            kept = [part for part in parts if canonical_winws2_lua_init_path(part) is None]
            if len(kept) != len(parts):
                if not kept:
                    continue
                raw = " ".join(kept)
        lines.append(raw)
    # Один перевод строки в конце файла.
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _assert_block_invariants(test: unittest.TestCase, text: str, source_text: str) -> None:
    lines = text.split("\n")
    header_end = len(parse_preset_text(text, engine=ENGINE_WINWS2).header_lines)
    test.assertEqual(lines[header_end : header_end + len(WINWS2_LUA_INIT_LINES)], list(WINWS2_LUA_INIT_LINES))
    canonical_lines = [line for line in lines if canonical_winws2_lua_init_path(line.strip())]
    test.assertEqual(canonical_lines, list(WINWS2_LUA_INIT_LINES), "блок ровно один раз")
    preset = parse_preset_text(text, engine=ENGINE_WINWS2)
    test.assertEqual(preset.preamble_lines[: len(WINWS2_LUA_INIT_LINES)], list(WINWS2_LUA_INIT_LINES))
    for profile in preset.profiles:
        for segment in profile.segments:
            test.assertIsNone(canonical_winws2_lua_init_path(segment.text), "блок не внутри profile")
    # Прочие --lua-init пользователя сохранены в прежнем порядке.
    user_inits = [
        part
        for line in source_text.replace("\r\n", "\n").split("\n")
        for part in _options(line)
        if part.lower().startswith("--lua-init=") and canonical_winws2_lua_init_path(part) is None
    ]
    saved_inits = [
        part
        for line in lines
        for part in _options(line)
        if part.lower().startswith("--lua-init=") and canonical_winws2_lua_init_path(part) is None
    ]
    test.assertEqual(saved_inits, user_inits)


class LuaInitBlockTests(unittest.TestCase):
    def test_block_is_complete_once_at_preamble_start_and_idempotent(self) -> None:
        for name, source in WINWS2_FIXTURES.items():
            with self.subTest(fixture=name):
                result = ensure_winws2_lua_init_block(source)
                _assert_block_invariants(self, result, source)
                self.assertEqual(ensure_winws2_lua_init_block(result), result)

    def test_block_goes_after_header_comments(self) -> None:
        result = ensure_winws2_lua_init_block(WINWS2_FIXTURES["partial_block"])
        self.assertEqual(
            result.split("\n")[:3 + len(WINWS2_LUA_INIT_LINES) + 2],
            [
                "# Preset: Partial",
                "# Description: two lines only",
                "",
                *WINWS2_LUA_INIT_LINES,
                "",
                "--wf-tcp-out=443",
            ],
        )

    def test_block_is_never_inserted_inside_a_profile(self) -> None:
        # Старая логика ставила недостающие строки после последнего --lua-init,
        # то есть внутрь profile «discord».
        result = ensure_winws2_lua_init_block(WINWS2_FIXTURES["lua_init_inside_profile"])
        discord = parse_preset_text(result, engine=ENGINE_WINWS2).profiles[0]
        self.assertEqual(discord.name, "discord")
        self.assertNotIn("--lua-init=@lua/fakemultisplit.lua", [segment.text for segment in discord.segments])
        self.assertTrue(result.startswith("# Preset: Inside profile\n" + "\n".join(WINWS2_LUA_INIT_LINES) + "\n--wf-tcp-out=443\n"))

    def test_other_spellings_are_recognised_and_not_duplicated(self) -> None:
        result = ensure_winws2_lua_init_block(WINWS2_FIXTURES["other_spellings"])
        self.assertEqual(
            [line for line in result.split("\n") if "lua-init" in line.lower()],
            list(WINWS2_LUA_INIT_LINES),
        )

    def test_extra_file_on_shared_line_is_kept(self) -> None:
        result = ensure_winws2_lua_init_block(WINWS2_FIXTURES["duplicates"])
        self.assertEqual(
            result.split("\n")[: len(WINWS2_LUA_INIT_LINES) + 2],
            [*WINWS2_LUA_INIT_LINES, "--lua-init=@lua/my_extra.lua", "--wf-tcp-out=443"],
        )

    def test_inline_lua_code_stays_after_block(self) -> None:
        result = ensure_winws2_lua_init_block(WINWS2_FIXTURES["exp_inline_code"])
        lines = result.split("\n")
        self.assertEqual(lines[2 : 2 + len(WINWS2_LUA_INIT_LINES)], list(WINWS2_LUA_INIT_LINES))
        self.assertEqual(lines[2 + len(WINWS2_LUA_INIT_LINES)], _EXP_INLINE)

    def test_lua_init_without_at_is_inline_code_not_a_file(self) -> None:
        self.assertIsNone(canonical_winws2_lua_init_path("--lua-init=lua/zapret-lib.lua"))
        result = ensure_winws2_lua_init_block(WINWS2_FIXTURES["no_at_is_inline_code"])
        self.assertIn("\n--lua-init=lua/zapret-lib.lua\n", result)

    def test_unrelated_lua_files_are_not_canonical(self) -> None:
        for option in (
            "--lua-init=@lua/zapret-lib.lua.bak",
            "--lua-init=@lua/my-zapret-lib.lua",
            "--lua-init=@other/zapret-lib.lua",
            "--lua-desync=fake",
        ):
            with self.subTest(option=option):
                self.assertIsNone(canonical_winws2_lua_init_path(option))


class SaveNormalizationTests(unittest.TestCase):
    def test_contract_lists_each_save_normalization(self) -> None:
        self.assertEqual(len(SAVE_TIME_NORMALIZATIONS), 5)

    def test_winws1_text_gets_no_lua_init_block(self) -> None:
        for name, source in {**WINWS1_FIXTURES, **_builtin_texts(ENGINE_WINWS1)}.items():
            with self.subTest(preset=name):
                self.assertNotIn("--lua-init", normalize_preset_source_for_save(source, ENGINE_WINWS1))

    def test_every_builtin_winws2_preset_is_a_save_fixed_point(self) -> None:
        # Builtin уже в формате пресета: сохранение без правок ничего в нём не
        # меняет и не порождает пользовательскую копию.
        for name, text in _builtin_texts(ENGINE_WINWS2).items():
            with self.subTest(preset=name):
                self.assertEqual(normalize_preset_source_for_save(text, ENGINE_WINWS2), text)
                _assert_block_invariants(self, text, text)
                self.assertIn("# BuiltinVersion: 2.42", text.split("\n")[:5])

    def test_every_builtin_winws2_preset_passes_launch_validation(self) -> None:
        from winws_runtime.preset_launch_text import (
            is_winws2_circular_preset_text,
            prepare_winws2_preset_text_for_launch,
        )

        for name, text in _builtin_texts(ENGINE_WINWS2).items():
            with self.subTest(preset=name):
                prepared = prepare_winws2_preset_text_for_launch(
                    text,
                    source_name=name,
                    source_is_circular=is_winws2_circular_preset_text(text),
                )
                self.assertEqual(prepared.text, text)


class _UiStore:
    def __init__(self) -> None:
        self.notify_preset_content_changed = Mock()
        self.notify_presets_changed = Mock()
        self.notify_preset_switched = Mock()
        self.notify_preset_identity_changed = Mock()


class SaveIdentityTests(unittest.TestCase):
    """save / duplicate / create / import через настоящие PresetFileService и PresetFileStore."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = AppPaths(user_root=self.root, local_root=self.root)
        self.store = PresetFileStore(self.paths)
        self.selected: dict[str, str] = {}

    def _service(self, engine: str) -> PresetFileService:
        store = self.store
        selected = self.selected

        class _Coordinator:
            def get_selected_source_manifest(self, _launch_method):
                file_name = selected.get(engine, "")
                return store.get_manifest(engine, file_name) if file_name else None

            def refresh_selected_launch_preset(self, _launch_method):
                return None

        ui_store = _UiStore()
        return PresetFileService(
            engine=engine,
            launch_method=LAUNCH_METHOD_BY_ENGINE[engine],
            app_paths=self.paths,
            preset_mode_coordinator=_Coordinator(),
            preset_file_store=self.store,
            preset_selection_service=SimpleNamespace(),
            preset_store_winws2=ui_store,
            preset_store_winws1=ui_store,
        )

    def _assert_identity(self, engine: str, source: str, stored: str) -> None:
        self.assertEqual(
            _erase_allowed_save_differences(stored, engine),
            _erase_allowed_save_differences(source, engine),
        )
        self.assertTrue(stored.endswith("\n") and not stored.endswith("\n\n"))
        self.assertNotIn("\r", stored)
        self.assertEqual(normalize_preset_source_for_save(stored, engine), stored)
        if engine == ENGINE_WINWS2:
            _assert_block_invariants(self, stored, source)

    def _cases(self, engine: str) -> dict[str, str]:
        fixtures = {**WINWS2_FIXTURES, **WINWS2_SAVE_ONLY_FIXTURES} if engine == ENGINE_WINWS2 else WINWS1_FIXTURES
        return {**{f"fixture:{name}": text for name, text in fixtures.items()}, **_builtin_texts(engine)}

    def test_save_duplicate_create_store_only_allowed_normalizations(self) -> None:
        for engine in (ENGINE_WINWS2, ENGINE_WINWS1):
            service = self._service(engine)
            base = self.store.create_preset(engine, "Base", "# Preset: Base\n--wf-tcp-out=443\n")
            for index, (name, source) in enumerate(self._cases(engine).items()):
                with self.subTest(engine=engine, case=name, operation="save"):
                    saved = service.save_source_text_by_file_name(base.file_name, source, publish_content_changed=False)
                    stored = self.store.read_source_text(engine, saved.file_name)
                    self._assert_identity(engine, source, stored)
                with self.subTest(engine=engine, case=name, operation="duplicate"):
                    duplicate = service.duplicate_by_file_name(base.file_name, f"Copy {index}")
                    self._assert_identity(engine, stored, self.store.read_source_text(engine, duplicate.file_name))
                with self.subTest(engine=engine, case=name, operation="create"):
                    self.selected[engine] = base.file_name
                    created = service.create(f"Created {index}", from_current=True)
                    self._assert_identity(engine, stored, self.store.read_source_text(engine, created.file_name))

    def test_import_stores_only_allowed_normalizations(self) -> None:
        from presets.preset_text_ops import validate_preset_source_text

        imports_dir = self.root / "imports"
        imports_dir.mkdir()
        for engine in (ENGINE_WINWS2, ENGINE_WINWS1):
            service = self._service(engine)
            for index, (name, source) in enumerate(self._cases(engine).items()):
                if validate_preset_source_text(source, engine=engine):
                    continue
                with self.subTest(engine=engine, case=name):
                    src = imports_dir / f"{engine}_{index}.txt"
                    src.write_bytes(source.encode("utf-8"))
                    imported = service.import_from_file(src, f"Imported {index}")
                    stored = self.store.read_source_text(engine, imported.file_name)
                    self._assert_identity(engine, source, stored)
                    self.assertIn("# PresetKind: imported", stored.split("\n")[:3])

    def test_import_decodes_utf8_bom_as_encoding_not_text(self) -> None:
        service = self._service(ENGINE_WINWS2)
        src = self.root / "bom.txt"
        src.write_bytes("\ufeff".encode("utf-8") + WINWS2_FIXTURES["no_block"].encode("utf-8"))

        imported = service.import_from_file(src, "Bom")
        stored = self.store.read_source_text(ENGINE_WINWS2, imported.file_name)

        self.assertNotIn("\ufeff", stored)
        self.assertEqual(stored.split("\n")[:2], ["# Preset: Bom", "# PresetKind: imported"])
        self._assert_identity(ENGINE_WINWS2, WINWS2_FIXTURES["no_block"], stored)

    def test_bom_on_disk_is_not_carried_into_duplicate_or_save(self) -> None:
        service = self._service(ENGINE_WINWS2)
        user_dir = self.paths.engine_paths(ENGINE_WINWS2).ensure_directories().user_presets_dir
        source = WINWS2_SAVE_ONLY_FIXTURES["bom_first_line"]
        (user_dir / "Bom.txt").write_bytes(source.encode("utf-8"))

        self.assertEqual(self.store.read_source_text(ENGINE_WINWS2, "Bom.txt"), source[1:])
        duplicate = service.duplicate_by_file_name("Bom.txt", "Bom copy")
        stored = self.store.read_source_text(ENGINE_WINWS2, duplicate.file_name)
        self.assertEqual(stored.split("\n")[0], "# Preset: Bom copy")
        self.assertNotIn("\ufeff", (user_dir / duplicate.file_name).read_text(encoding="utf-8"))
        self._assert_identity(ENGINE_WINWS2, source, stored)

        service.save_source_text_by_file_name("Bom.txt", source + "--new\n--filter-udp=443\n", publish_content_changed=False)
        saved_bytes = (user_dir / "Bom.txt").read_bytes()
        self.assertFalse(saved_bytes.startswith("\ufeff".encode("utf-8")))
        self.assertEqual(saved_bytes.decode("utf-8").split("\n")[0], "# Preset: Bom")

    def test_saving_unchanged_builtin_creates_no_user_copy(self) -> None:
        for engine in (ENGINE_WINWS2,):
            builtin_dir = self.paths.engine_paths(engine).ensure_directories().builtin_presets_dir
            for name, text in list(_builtin_texts(engine).items())[:5]:
                (builtin_dir / name).write_bytes(text.encode("utf-8"))
                with self.subTest(preset=name):
                    self._service(engine).save_source_text_by_file_name(name, text, publish_content_changed=False)
                    self.assertFalse((self.paths.engine_paths(engine).user_presets_dir / name).exists())


if __name__ == "__main__":
    unittest.main()
